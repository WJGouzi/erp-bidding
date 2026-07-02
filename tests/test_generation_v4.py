"""单元测试：v4 分段生成路由。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock

from app.service_modules.task_pipeline.helpers import (
    _generate_chapter_content_v4,
    _hard_generate,
    _soft_generate,
    _free_generate,
    _EMPTY_PAGE_MARKER,
)


class FakeTask:
    def __init__(self):
        self.bid_type = "GOODS"
        self.catalog_generation_level = None
        self.word_count_level = None
        self.selected_package_no = None
        self.use_knowledge_base = False
        self.knowledge_base_ids = None


class FakeAnalysisResult:
    def __init__(self, effective_text=""):
        self.effective_text = effective_text
        self.raw_text = effective_text
        self.analysis_data = '{}'


def _make_app_context():
    """创建最小 Flask app context。"""
    from flask import Flask
    app = Flask(__name__)
    app.config["OPENAI_API_KEY"] = "test"
    app.config["OPENAI_BASE_URL"] = "http://test"
    app.config["OPENAI_MODEL_NAME"] = "test"
    app.config["MIN_RECALL_CONFIDENCE"] = 0.3
    app.config["GENERATE_SIMULATE_DELAY"] = 0
    app.config["CHROMA_HOST"] = "localhost"
    app.config["CHROMA_PORT"] = 8000
    app.config["FLASK_ENV"] = "TESTING"
    return app.app_context()


class TestV4Routing(unittest.TestCase):
    """测试 v4 路由是否正确分发。"""

    def setUp(self):
        self.task = FakeTask()
        self.analysis = FakeAnalysisResult()

    def _call(self, chapter, subject=None, kb=None, product=None):
        with _make_app_context():
            return _generate_chapter_content_v4(
                self.task, chapter, self.analysis,
                subject or {}, kb or {}, product or {},
            )

    def test_mandate_hard_routes_to_hard(self):
        result = self._call({"title": "投标函", "mandate_level": "HARD"})
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    def test_mandate_soft_routes_to_soft(self):
        result = self._call({"title": "技术方案", "mandate_level": "SOFT"})
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    def test_mandate_free_routes_to_free(self):
        result = self._call({"title": "综合方案", "mandate_level": "FREE"})
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    @patch('app.service_modules.task_pipeline.helpers.LLMAdapter')
    @patch('app.service_modules.task_pipeline.helpers.ChromaAdapter')
    def test_no_mandate_falls_back(self, mock_chroma, mock_llm):
        mock_llm_instance = MagicMock()
        mock_llm_instance.is_available.return_value = True
        mock_llm_instance.generate_text.return_value = "综合响应内容"
        mock_llm.return_value = mock_llm_instance
        with _make_app_context():
            result = _generate_chapter_content_v4(
                self.task, {"title": "综合响应"}, self.analysis,
                {}, {}, {},
            )
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        self.assertIsInstance(result, str)

    def test_bound_segments_passed(self):
        result = self._call({"title": "技术方案", "mandate_level": "SOFT",
                             "bound_segments": ["sec_3", "sec_4"]})
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    def test_guardrails_passed(self):
        result = self._call({"title": "综合方案", "mandate_level": "FREE",
                             "guardrails": [
                                 {"type": "EVIDENCE_REQUIRED", "detail": "业绩需有合同证明"},
                             ]})
        self.assertEqual(result, _EMPTY_PAGE_MARKER)


class TestHardGenerate(unittest.TestCase):
    """HARD 路径测试。"""

    def test_empty_analysis_returns_placeholder(self):
        result = _hard_generate(
            FakeTask(),
            {"title": "投标函"},
            FakeAnalysisResult(),
            {},
        )
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    @patch('app.service_modules.task_pipeline.helpers.LLMAdapter')
    @patch('app.service_modules.task_pipeline.helpers.ChromaAdapter')
    def test_has_effective_text(self, mock_chroma, mock_llm):
        mock_llm.return_value.is_available.return_value = False
        task = FakeTask()
        analysis = FakeAnalysisResult(effective_text="投标函\n致：采购人\n我方承诺参与投标")
        chapter = {"title": "投标函"}
        result = _hard_generate(task, chapter, analysis, {})
        self.assertIsInstance(result, str)


class TestSoftGenerate(unittest.TestCase):
    """SOFT 路径测试。"""

    def setUp(self):
        self.task = FakeTask()
        self.analysis = FakeAnalysisResult()
        self.chapter = {"title": "技术方案", "bound_segments": ["sec_3"]}

    def _call(self, subject=None, kb=None, product=None, guardrails=None):
        with _make_app_context():
            return _soft_generate(
                self.task, self.chapter, self.analysis,
                subject or {}, kb or {}, product or [],
                guardrails or [],
            )

    def test_no_materials_returns_placeholder(self):
        result = self._call()
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    def test_subject_material_included(self):
        subject = {"materials": [{"text_excerpt": "我方具有独立承担民事责任的能力。"}]}
        result = self._call(subject=subject)
        self.assertIn("独立承担民事责任", result)

    def test_knowledge_base_structured_snippet(self):
        kb = {"knowledge_list": [{"knowledge_base_name": "历史标书",
                                  "snippets": [{"text": "我公司提供7×24小时技术支持服务。",
                                                 "score": 0.92, "confidence": "HIGH"}]}]}
        result = self._call(kb=kb)
        self.assertIn("7×24", result)
        self.assertIn("技术支持", result)

    def test_knowledge_base_legacy_string_snippet(self):
        kb = {"knowledge_list": [{"knowledge_base_name": "历史标书",
                                  "snippets": ["我公司具有ISO9001认证。"]}]}
        result = self._call(kb=kb)
        self.assertIn("ISO9001", result)

    def test_product_material_included(self):
        product = {"matched_products": [{"matched_text": "产品型号：ABC-2000"}]}
        result = self._call(product=product)
        self.assertIn("ABC-2000", result)

    def test_guardrails_not_in_output(self):
        result = self._call(guardrails=[{"type": "MANDATORY_MATCH", "detail": "★参数必须响应"}])
        self.assertEqual(result, _EMPTY_PAGE_MARKER)


class TestFreeGenerate(unittest.TestCase):
    """FREE 路径测试。"""

    def setUp(self):
        self.task = FakeTask()
        self.analysis = FakeAnalysisResult()

    def _call(self, chapter=None):
        with _make_app_context():
            return _free_generate(
                self.task, chapter or {"title": "综合方案"}, self.analysis,
                {}, {}, [], [],
            )

    def test_no_materials_returns_placeholder(self):
        result = self._call()
        self.assertEqual(result, _EMPTY_PAGE_MARKER)

    @patch('app.service_modules.task_pipeline.helpers.LLMAdapter')
    def test_subject_materials_provided(self, mock_llm):
        mock_llm.return_value.is_available.return_value = True
        mock_llm.return_value.generate_text.return_value = "我方承诺提供3年免费质保服务。"
        with _make_app_context():
            result = _free_generate(
                self.task, {"title": "服务方案"}, self.analysis,
                {"materials": [{"text_excerpt": "我方承诺提供3年免费质保。"}]},
                {}, [], [],
            )
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
