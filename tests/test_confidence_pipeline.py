"""单元测试：置信度在知识库管道中的保留与兼容。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 从 helpers 导入受影响的函数
from app.service_modules.task_pipeline.helpers import _any_snippet_has_text


class TestAnySnippetHasText(unittest.TestCase):
    """测试 _any_snippet_has_text 函数兼容新旧格式。"""

    def test_new_format_dict_with_text(self):
        """新格式 dict 含有文本应返回 True。"""
        snippets = [
            {"text": "有效的知识库片段内容", "score": 0.85, "confidence": "HIGH"},
        ]
        self.assertTrue(_any_snippet_has_text(snippets))

    def test_new_format_dict_empty_text(self):
        """新格式 dict 文本为空应返回 False。"""
        snippets = [
            {"text": "", "score": 0.0, "confidence": "UNKNOWN"},
        ]
        self.assertFalse(_any_snippet_has_text(snippets))

    def test_old_format_string(self):
        """旧格式字符串应正常工作。"""
        snippets = ["旧格式文本片段"]
        self.assertTrue(_any_snippet_has_text(snippets))

    def test_old_format_empty_string(self):
        """旧格式空字符串应返回 False。"""
        snippets = [""]
        self.assertFalse(_any_snippet_has_text(snippets))

    def test_mixed_format(self):
        """混用新旧格式应正确识别。"""
        snippets = [
            {"text": "新格式文本", "score": 0.9, "confidence": "HIGH"},
            "",
            {"text": "", "score": 0.0, "confidence": "UNKNOWN"},
            "旧格式文本",
        ]
        self.assertTrue(_any_snippet_has_text(snippets))

    def test_all_empty(self):
        """所有条目都是空的应返回 False。"""
        snippets = [
            {"text": "", "score": 0.0},
            "",
            {"text": None},
        ]
        self.assertFalse(_any_snippet_has_text(snippets))

    def test_empty_list(self):
        self.assertFalse(_any_snippet_has_text([]))

    def test_none_text_in_dict(self):
        snippets = [{"score": 0.5, "confidence": "MEDIUM"}]  # 无 text 键
        self.assertFalse(_any_snippet_has_text(snippets))

    def test_whitespace_only_text(self):
        snippets = [{"text": "   ", "score": 0.5}]
        self.assertFalse(_any_snippet_has_text(snippets))

    def test_long_valid_text(self):
        text = "有效内容。" * 50
        snippets = [{"text": text, "score": 0.95, "confidence": "HIGH"}]
        self.assertTrue(_any_snippet_has_text(snippets))


class TestConfidenceLevelMapping(unittest.TestCase):
    """测试 ConfidenceLevel 与召回分数的映射。"""

    def test_confidence_from_score(self):
        from app.domain.analysis_schema import ConfidenceLevel
        self.assertEqual(ConfidenceLevel.from_value(0.95), ConfidenceLevel.EXACT)
        self.assertEqual(ConfidenceLevel.from_value(0.85), ConfidenceLevel.HIGH)
        self.assertEqual(ConfidenceLevel.from_value(0.70), ConfidenceLevel.MEDIUM)
        self.assertEqual(ConfidenceLevel.from_value(0.50), ConfidenceLevel.LOW)
        self.assertEqual(ConfidenceLevel.from_value(0.30), ConfidenceLevel.UNCERTAIN)
        self.assertEqual(ConfidenceLevel.from_value(0.0), ConfidenceLevel.UNKNOWN)

    def test_confidence_name(self):
        from app.domain.analysis_schema import ConfidenceLevel
        self.assertEqual(ConfidenceLevel.from_value(0.96).name, "EXACT")    # >= 0.95
        self.assertEqual(ConfidenceLevel.from_value(0.90).name, "HIGH")     # 0.85-0.949
        self.assertEqual(ConfidenceLevel.from_value(0.80).name, "MEDIUM")   # 0.70-0.849
        self.assertEqual(ConfidenceLevel.from_value(0.60).name, "LOW")      # 0.50-0.699
        self.assertEqual(ConfidenceLevel.from_value(0.40).name, "UNCERTAIN")# 0.30-0.499

    def test_confidence_score_threshold(self):
        """验证边界值的映射正确性。"""
        from app.domain.analysis_schema import ConfidenceLevel
        # EXACT >= 0.95
        self.assertEqual(ConfidenceLevel.from_value(0.949).name, "HIGH")
        self.assertEqual(ConfidenceLevel.from_value(0.95).name, "EXACT")
        # HIGH >= 0.85
        self.assertEqual(ConfidenceLevel.from_value(0.849).name, "MEDIUM")
        self.assertEqual(ConfidenceLevel.from_value(0.85).name, "HIGH")


class TestFilterLowConfidenceSnippets(unittest.TestCase):
    """测试 _filter_low_confidence_kb_snippets 兼容新旧格式。"""

    def setUp(self):
        from app.service_modules.task_pipeline.helpers import _filter_low_confidence_kb_snippets
        self._filter = _filter_low_confidence_kb_snippets

    def test_filter_new_format_keeps_high(self):
        context = {
            "knowledge_list": [{
                "knowledge_base_name": "test",
                "snippets": [
                    {"text": "高置信度内容", "score": 0.85, "confidence": "HIGH"},
                    {"text": "低置信度内容", "score": 0.25, "confidence": "LOW"},
                ],
            }]
        }
        result = self._filter(context, min_score=0.3)
        snippets = result["knowledge_list"][0]["snippets"]
        self.assertEqual(len(snippets), 1)
        if isinstance(snippets[0], dict):
            self.assertEqual(snippets[0]["confidence"], "HIGH")
        else:
            self.fail("期望保留 dict 格式")

    def test_filter_old_format(self):
        context = {
            "knowledge_list": [{
                "knowledge_base_name": "test",
                "snippets": ["短文本", "足够长的有效内容文本用于测试过滤功能。"],
            }]
        }
        result = self._filter(context, min_score=0.3)
        snippets = result["knowledge_list"][0]["snippets"]
        # 短文本置信度低，应被过滤
        self.assertEqual(len(snippets), 1)
        self.assertIsInstance(snippets[0], str)

    def test_filter_empty(self):
        result = self._filter({})
        self.assertEqual(result, {})

    def test_filter_none(self):
        result = self._filter(None)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
