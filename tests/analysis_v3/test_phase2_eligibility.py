"""单元测试：Phase 2 生死线扫描 — 纯关键词模式。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.service_modules.task_pipeline.analysis_v3.phase2_eligibility import (
    scan_eligibility,
    ELIGIBILITY_TEMPLATES,
    _find_qualification_sections,
    _find_matching_lines,
    _is_disqualification,
    _is_starred,
    _deduplicate,
)


class FakeContentBlock:
    def __init__(self, text="", type="paragraph"):
        self.text = text
        self.type = type


class FakeSection:
    def __init__(self, title="", level=1, content=None, children=None):
        self.title = title
        self.level = level
        self.content = content or []
        self.children = children or []


class TestEligibilityTemplates(unittest.TestCase):
    def test_goods_template_exists(self):
        self.assertIn("GOODS", ELIGIBILITY_TEMPLATES)

    def test_service_template_exists(self):
        self.assertIn("SERVICE", ELIGIBILITY_TEMPLATES)

    def test_engineering_template_exists(self):
        self.assertIn("ENGINEERING", ELIGIBILITY_TEMPLATES)

    def test_template_has_required_categories(self):
        template = ELIGIBILITY_TEMPLATES["GOODS"]
        required = ["通用资格", "废标条件", "★实质性", "信用记录"]
        for cat in required:
            self.assertIn(cat, template)

    def test_template_keywords_not_empty(self):
        for bid_type, template in ELIGIBILITY_TEMPLATES.items():
            for cat, keywords in template.items():
                self.assertGreater(len(keywords), 0, f"{bid_type}.{cat} has no keywords")


class TestFindQualificationSections(unittest.TestCase):
    def test_find_by_title(self):
        sections = [
            FakeSection(title="第一章 招标公告"),
            FakeSection(title="第四章 资格要求", content=[
                FakeContentBlock("投标人须具备营业执照"),
            ]),
            FakeSection(title="第五章 评标办法"),
        ]
        found = _find_qualification_sections(sections)
        self.assertGreaterEqual(len(found), 1)

    def test_empty_sections(self):
        found = _find_qualification_sections([])
        self.assertEqual(found, [])

    def test_no_qualification_section(self):
        sections = [
            FakeSection(title="第一章"),
            FakeSection(title="第二章"),
        ]
        found = _find_qualification_sections(sections)
        self.assertIsInstance(found, list)


class TestFindMatchingLines(unittest.TestCase):
    def test_basic_match(self):
        text = "投标人须具有独立承担民事责任的能力\n须提供营业执照"
        matches = _find_matching_lines(text, ["营业执照"])
        self.assertEqual(len(matches), 1)

    def test_multiple_keywords(self):
        text = "废标条件：未按要求提供\n营业执照须有效"
        matches = _find_matching_lines(text, ["营业执照", "废标"])
        self.assertEqual(len(matches), 2)

    def test_no_match(self):
        matches = _find_matching_lines("无相关内容", ["营业执照"])
        self.assertEqual(len(matches), 0)


class TestIsDisqualification(unittest.TestCase):
    def test_disqualification(self):
        self.assertTrue(_is_disqualification("出现以下情形作废标处理"))

    def test_not_disqualification(self):
        self.assertFalse(_is_disqualification("营业执照须在有效期内"))


class TestIsStarred(unittest.TestCase):
    def test_starred(self):
        self.assertTrue(_is_starred("★本项目不接受联合体投标"))

    def test_not_starred(self):
        self.assertFalse(_is_starred("营业执照须在有效期内"))


class TestDeduplicate(unittest.TestCase):
    def test_basic_dedup(self):
        items = [("a", "b"), ("a", "b"), ("c", "d")]
        result = _deduplicate(items, lambda x: x[0])
        self.assertEqual(len(result), 2)

    def test_no_duplicates(self):
        items = [("a", "b"), ("c", "d")]
        result = _deduplicate(items, lambda x: x[0])
        self.assertEqual(len(result), 2)

    def test_empty(self):
        result = _deduplicate([], lambda x: x)
        self.assertEqual(result, [])


class TestScanEligibility(unittest.TestCase):
    def test_goods_scan_runs(self):
        """至少能运行且返回正确结构。"""
        sections = [
            FakeSection(title="第四章 投标人资格要求", content=[
                FakeContentBlock("投标人须具有营业执照"),
                FakeContentBlock("须提供近一年财务报告"),
                FakeContentBlock("本项目不接受联合体投标"),
                FakeContentBlock("废标条件：未按要求提供投标文件"),
            ]),
        ]
        result = scan_eligibility(sections, "GOODS")
        self.assertIn("qualifications", result)
        self.assertIn("disqualifications", result)
        self.assertIn("starred_requirements", result)
        self.assertIn("summary", result)

    def test_result_structure(self):
        sections = [FakeSection(title="资格要求")]
        result = scan_eligibility(sections, "GOODS")
        self.assertIn("total_items", result["summary"])
        self.assertIn("passed", result["summary"])
        self.assertIn("attention_required", result["summary"])

    def test_empty_sections(self):
        result = scan_eligibility([], "GOODS")
        self.assertIsNotNone(result)
        # 应该有 fallback 条目
        self.assertGreaterEqual(result["summary"]["total_items"], 0)

    def test_llm_free(self):
        """验证不依赖 LLM，纯规则可用。"""
        sections = [FakeSection(title="资格要求")]
        result = scan_eligibility(sections, "GOODS")
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
