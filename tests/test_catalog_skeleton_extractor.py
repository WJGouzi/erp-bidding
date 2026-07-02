"""单元测试：目录骨架提取器。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.service_modules.task_pipeline.catalog_skeleton_extractor import (
    extract_skeleton_from_tender,
    find_format_section,
    _clean_title,
)


class TestFindFormatSection(unittest.TestCase):
    """查找"投标文件组成"章节测试。"""

    def test_exact_match(self):
        index = [
            {"id": "sec_1", "title": "第一章 招标公告"},
            {"id": "sec_2", "title": "第二章 投标人须知"},
            {"id": "sec_3", "title": "投标文件的编制", "children": [
                {"id": "sec_3_1", "title": "一、投标函"},
            ]},
        ]
        result = find_format_section(index)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "投标文件的编制")

    def test_fuzzy_match(self):
        index = [
            {"id": "sec_1", "title": "一、投标文件组成", "children": [
                {"id": "sec_1_1", "title": "投标函"},
            ]},
        ]
        result = find_format_section(index)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "一、投标文件组成")

    def test_no_match(self):
        index = [
            {"id": "sec_1", "title": "第一章 招标公告"},
            {"id": "sec_2", "title": "第二章 投标人须知"},
        ]
        result = find_format_section(index)
        self.assertIsNone(result)

    def test_nested_search(self):
        index = [
            {"id": "sec_1", "title": "总则", "children": [
                {"id": "sec_1_1", "title": "投标须知"},
                {"id": "sec_1_2", "title": "应提交的文件", "children": [
                    {"id": "sec_1_2_1", "title": "资格证明文件"},
                ]},
            ]},
        ]
        result = find_format_section(index)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "sec_1_2")

    def test_empty_index(self):
        self.assertIsNone(find_format_section([]))


class TestExtractSkeletonFromTender(unittest.TestCase):
    """从招标文件提取目录骨架测试。"""

    def test_basic_extraction(self):
        index = [
            {"id": "sec_3", "title": "投标文件组成", "children": [
                {"id": "sec_3_1", "title": "一、投标函", "children": []},
                {"id": "sec_3_2", "title": "二、报价一览表", "children": []},
                {"id": "sec_3_3", "title": "三、资格证明文件", "children": [
                    {"id": "sec_3_3_1", "title": "（一）营业执照"},
                ]},
            ]},
        ]
        result = extract_skeleton_from_tender(index)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["title"], "投标函")
        self.assertEqual(result[1]["title"], "报价一览表")
        self.assertEqual(result[2]["title"], "资格证明文件")
        # 子节点应包含（二级深度）
        self.assertEqual(len(result[2]["children"]), 1)
        self.assertEqual(result[2]["children"][0]["title"], "营业执照")

    def test_source_section_id(self):
        index = [
            {"id": "sec_5", "title": "投标文件的组成", "children": [
                {"id": "sec_5_1", "title": "商务部分"},
            ]},
        ]
        result = extract_skeleton_from_tender(index)
        self.assertEqual(result[0]["source_section_id"], "sec_5_1")
        self.assertEqual(result[0]["source"], "tender_document")

    def test_no_format_section(self):
        result = extract_skeleton_from_tender([
            {"id": "sec_1", "title": "招标公告"},
        ])
        self.assertIsNone(result)

    def test_empty_children(self):
        index = [
            {"id": "sec_3", "title": "投标文件格式", "children": []},
        ]
        result = extract_skeleton_from_tender(index)
        self.assertIsNone(result)

    def test_skip_toc_noise(self):
        index = [
            {"id": "sec_3", "title": "投标文件的编制", "children": [
                {"id": "sec_3_0", "title": "目录"},
                {"id": "sec_3_1", "title": "一、投标函"},
                {"id": "sec_3_2", "title": "二、资格证明"},
                {"id": "sec_3_3", "title": "三、技术方案"},
            ]},
        ]
        result = extract_skeleton_from_tender(index)
        self.assertIsNotNone(result)
        titles = [r["title"] for r in result]
        self.assertNotIn("目录", titles)
        self.assertEqual(len(result), 3)

    def test_clean_title_prefix(self):
        """标题中的序号前缀应被清理。"""
        index = [
            {"id": "sec_3", "title": "投标文件组成", "children": [
                {"id": "sec_3_1", "title": "一、投标函"},
                {"id": "sec_3_2", "title": "1. 报价部分"},
                {"id": "sec_3_3", "title": "（一）资格证明"},
            ]},
        ]
        result = extract_skeleton_from_tender(index)
        self.assertEqual(result[0]["title"], "投标函")
        self.assertEqual(result[1]["title"], "报价部分")
        self.assertEqual(result[2]["title"], "资格证明")

    def test_skip_numeric_only(self):
        """纯数字标题（页码）应被跳过。"""
        index = [
            {"id": "sec_3", "title": "投标文件的编制", "children": [
                {"id": "sec_3_1", "title": "一、投标函"},
                {"id": "sec_3_2", "title": "2"},
            ]},
        ]
        result = extract_skeleton_from_tender(index)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "投标函")

    def test_max_depth(self):
        index = [
            {"id": "sec_3", "title": "投标文件组成", "children": [
                {"id": "sec_3_1", "title": "技术方案", "children": [
                    {"id": "sec_3_1_1", "title": "实施计划", "children": [
                        {"id": "sec_3_1_1_1", "title": "详细步骤"},
                    ]},
                ]},
            ]},
        ]
        result = extract_skeleton_from_tender(index, max_depth=2)
        # depth 层次: 投标文件组成(0) → 技术方案(1) → 实施计划(2) → 详细步骤(3)
        # max_depth=2 意味着 depth < 2 时提取子节点
        # 技术方案(depth=1) 有子节点 → 实施计划
        self.assertEqual(len(result[0]["children"]), 1)
        self.assertEqual(result[0]["children"][0]["title"], "实施计划")
        # 实施计划(depth=2) 有子节点 → 详细步骤
        self.assertEqual(len(result[0]["children"][0]["children"]), 1)
        self.assertEqual(result[0]["children"][0]["children"][0]["title"], "详细步骤")
        # 详细步骤(depth=3) depth >= max_depth → 无子节点
        self.assertEqual(result[0]["children"][0]["children"][0]["children"], [])


class TestCleanTitle(unittest.TestCase):
    def test_chinese_number_prefix(self):
        self.assertEqual(_clean_title("一、投标函"), "投标函")
        self.assertEqual(_clean_title("二、技术方案"), "技术方案")

    def test_arabic_number_prefix(self):
        self.assertEqual(_clean_title("1. 报价部分"), "报价部分")
        self.assertEqual(_clean_title("1、资格证明"), "资格证明")

    def test_parenthesized_prefix(self):
        self.assertEqual(_clean_title("（一）营业执照"), "营业执照")
        self.assertEqual(_clean_title("（二）资质证书"), "资质证书")

    def test_no_prefix(self):
        self.assertEqual(_clean_title("技术方案"), "技术方案")

    def test_empty(self):
        self.assertEqual(_clean_title(""), "")
        self.assertEqual(_clean_title("  "), "")


if __name__ == "__main__":
    unittest.main()
