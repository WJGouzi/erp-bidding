"""单元测试：强制条款识别器 — 规则优先，LLM兜底。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.infrastructure.mandate_classifier import (
    classify_mandate,
    batch_classify,
    MANDATE_HARD,
    MANDATE_SOFT,
    MANDATE_FREE,
    HARD_EXACT_TITLES,
    HARD_TITLE_PATTERNS,
    HARD_CONTENT_PATTERNS,
    HARD_PARENT_KEYWORDS,
)


class TestMandateExactTitle(unittest.TestCase):
    """第一档：精确标题匹配测试。"""

    def test_in_exact_list(self):
        """白名单中的标题应返回 HARD。"""
        for title in [
            "投标函",
            "法定代表人身份证明",
            "授权委托书",
            "廉洁承诺书",
            "中小企业声明函",
            "开标一览表",
            "技术规格偏离表",
            "联合体协议书",
            "投标保证金缴纳凭证",
            "投标人基本情况表",
        ]:
            with self.subTest(title=title):
                result = classify_mandate(title)
                self.assertEqual(
                    result["level"], MANDATE_HARD,
                    f"'{title}' 应识别为 HARD，实际: {result['level']}"
                )
                self.assertEqual(result["source"], "rule:exact_title")

    def test_not_in_exact_list(self):
        """不在白名单但不像标题的内容应不命中精确匹配。"""
        result = classify_mandate("技术方案")
        self.assertNotEqual(result["source"], "rule:exact_title")

    def test_exact_titles_frozenset_completeness(self):
        """HARD_EXACT_TITLES 应包含常见的格式文件标题。"""
        expected_common_titles = {
            "投标函", "授权委托书", "廉洁承诺书",
            "中小企业声明函", "开标一览表", "联合体协议书",
            "法定代表人身份证明", "技术规格偏离表",
        }
        for t in expected_common_titles:
            self.assertIn(t, HARD_EXACT_TITLES, f"'{t}' 应在 HARD_EXACT_TITLES 中")


class TestMandateTitlePattern(unittest.TestCase):
    """第二档：标题模式匹配测试。"""

    def test_title_ends_with_声明(self):
        result = classify_mandate("关于知识产权的声明")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:title_pattern")

    def test_title_ends_with_承诺(self):
        result = classify_mandate("产品质量承诺")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:title_pattern")

    def test_title_ends_with_函(self):
        result = classify_mandate("质保期承诺函")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:title_pattern")

    def test_title_ends_with_格式(self):
        result = classify_mandate("投标文件格式")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertTrue(result["source"].startswith("rule:"))

    def test_title_starts_with_承诺(self):
        result = classify_mandate("承诺内容")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:title_pattern")

    def test_title_starts_with_声明(self):
        result = classify_mandate("声明事项")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:title_pattern")

    def test_normal_title_not_matched(self):
        """普通技术类标题不应被模式匹配命中。"""
        for title in ["技术方案", "实施方案", "项目管理"]:
            with self.subTest(title=title):
                result = classify_mandate(title)
                self.assertNotEqual(result["source"], "rule:title_pattern",
                                    f"'{title}' 不应被标题模式识别")


class TestMandateContentPattern(unittest.TestCase):
    """第三档：内容特征匹配测试。"""

    def test_content_致招标人(self):
        result = classify_mandate("报价函", text="致：采购人 ABC 公司")
        self.assertEqual(result["level"], MANDATE_HARD)
        # "报价函"标题命中 rule:title_pattern（函结尾），优先于内容模式
        self.assertEqual(result["source"], "rule:title_pattern")

    def test_content_盖章占位(self):
        result = classify_mandate("相关材料", text="供应商（盖章）：\n法定代表人（签字）：")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:content_pattern")

    def test_content_特此声明(self):
        result = classify_mandate("有关说明", text="特此声明。")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:content_pattern")

    def test_content_有效期(self):
        result = classify_mandate("投标说明", text="本投标文件有效期为 90 天。")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:content_pattern")

    def test_content_法定代表人签字(self):
        result = classify_mandate("文件签署", text="法定代表人（签字）：张三")
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:content_pattern")

    def test_short_text_no_match(self):
        """短文本且无特征不应误判。"""
        result = classify_mandate("项目概况", text="本项目为货物类采购。")
        self.assertNotEqual(result["source"], "rule:content_pattern")


class TestMandatePosition(unittest.TestCase):
    """第四档：位置特征匹配测试。"""

    def test_in_投标文件组成(self):
        result = classify_mandate(
            "商务部分",
            parent_title_chain=["投标文件的编制"],
        )
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:position")

    def test_in_应提交的文件(self):
        result = classify_mandate(
            "资格证明文件",
            parent_title_chain=["第一章", "应提交的文件"],
        )
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:position")

    def test_in_投标文件组成(self):
        result = classify_mandate(
            "报价文件",
            parent_title_chain=["投标文件组成"],
        )
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:position")

    def test_not_in_position(self):
        """不在特殊位置的章节不应被位置特征误判。"""
        result = classify_mandate(
            "技术方案",
            parent_title_chain=["第三章", "采购需求"],
        )
        self.assertNotEqual(result["source"], "rule:position")


class TestMandateTableType(unittest.TestCase):
    """第五档：表格类型匹配测试。"""

    def test_qualification_check_table(self):
        result = classify_mandate(
            "资格性审查表",
            table_types=["QUALIFICATION_CHECK"],
        )
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:table_type")

    def test_response_form_table(self):
        result = classify_mandate(
            "商务响应表",
            table_types=["RESPONSE_FORM"],
        )
        self.assertEqual(result["level"], MANDATE_HARD)
        self.assertEqual(result["source"], "rule:table_type")

    def test_other_table_not_hard(self):
        """非强制类型的表格不应被误判。"""
        result = classify_mandate(
            "产品清单",
            table_types=["PRODUCT_LIST"],
        )
        self.assertNotEqual(result["source"], "rule:table_type")


class TestMandateFreeContent(unittest.TestCase):
    """自由内容应返回 FREE 或 SOFT。"""

    def test_技术方案(self):
        result = classify_mandate("技术方案")
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))

    def test_服务方案(self):
        result = classify_mandate("服务方案")
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))

    def test_实施计划(self):
        result = classify_mandate("实施计划")
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))

    def test_项目管理(self):
        result = classify_mandate("项目管理")
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))

    def test_培训方案(self):
        result = classify_mandate("培训方案")
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))


class TestMandateEdgeCases(unittest.TestCase):
    """边界情况测试。"""

    def test_empty_title(self):
        result = classify_mandate("")
        self.assertEqual(result["level"], MANDATE_FREE)

    def test_whitespace_title(self):
        result = classify_mandate("  ")
        self.assertEqual(result["level"], MANDATE_FREE)

    def test_none_parent_chain(self):
        result = classify_mandate("技术方案", parent_title_chain=None)
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))

    def test_none_table_types(self):
        result = classify_mandate("技术方案", table_types=None)
        self.assertIn(result["level"], (MANDATE_FREE, MANDATE_SOFT))

    def test_long_content_text(self):
        """很长的正文只检查前500字符。"""
        # "普通段落内容。" = 7 字符，前 500/7 ≈ 71 个重复
        # 第72个重复后 + "致：招标人 ABC 公司" 超出500字符
        text = "普通段落内容。" * 72 + "致：招标人 ABC 公司"
        result = classify_mandate("普通章节", text=text)
        # 内容特征在500字符之外，不应被规则匹配到
        self.assertNotEqual(result["source"], "rule:content_pattern",
                            "500字符之外的内容不应被规则命中")

    def test_content_beyond_500(self):
        """特征在500字符之外不应被规则匹配。"""
        text = "普通段落。" * 100 + "致：招标人 ABC 公司"
        result = classify_mandate("普通章节", text=text)
        # "普通段落。" 重复100次 ≈ 500字符+
        # 所以"致"在500字符后，不应被规则内容匹配到
        self.assertNotEqual(result["source"], "rule:content_pattern")


class TestBatchClassify(unittest.TestCase):
    """批量识别测试。"""

    def test_batch_mixed(self):
        sections = [
            {"title": "廉洁承诺书"},
            {"title": "技术方案"},
            {"title": "声明函", "text": "本企业郑重声明"},
            {"title": "授权委托书", "parent_title_chain": ["投标文件组成"]},
        ]
        results = batch_classify(sections)
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0]["mandate"]["level"], MANDATE_HARD)
        self.assertEqual(results[1]["mandate"]["level"], MANDATE_FREE)
        self.assertEqual(results[2]["mandate"]["level"], MANDATE_HARD)
        self.assertEqual(results[3]["mandate"]["level"], MANDATE_HARD)

    def test_batch_empty(self):
        results = batch_classify([])
        self.assertEqual(results, [])

    def test_batch_preserves_original_fields(self):
        sections = [
            {"title": "技术方案", "description": "详细技术方案", "id": 1},
        ]
        results = batch_classify(sections)
        self.assertEqual(results[0]["description"], "详细技术方案")
        self.assertEqual(results[0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
