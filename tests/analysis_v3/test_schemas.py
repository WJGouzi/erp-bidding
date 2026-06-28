"""单元测试：JSON schema 与预处理。"""

import sys
import os
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.service_modules.task_pipeline.analysis_v3.schemas import (
    preprocess_json,
    safe_json_loads,
    assemble_v3_analysis_data,
    analysis_data_to_json,
    NULL_METADATA,
)


class TestPreprocessJson(unittest.TestCase):
    """测试 JSON 预处理函数。"""

    def test_trailing_comma_object(self):
        raw = '{"a": 1, "b": 2,}'
        cleaned = preprocess_json(raw)
        self.assertEqual(json.loads(cleaned), {"a": 1, "b": 2})

    def test_trailing_comma_array(self):
        raw = '{"items": [1, 2, 3,]}'
        cleaned = preprocess_json(raw)
        self.assertEqual(json.loads(cleaned), {"items": [1, 2, 3]})

    def test_control_chars(self):
        raw = '{"name": "test\x00value"}'
        cleaned = preprocess_json(raw)
        self.assertEqual(json.loads(cleaned), {"name": "testvalue"})

    def test_bom(self):
        raw = '\ufeff{"key": "value"}'
        cleaned = preprocess_json(raw)
        self.assertEqual(json.loads(cleaned), {"key": "value"})

    def test_markdown_codeblock(self):
        raw = '```json\n{"key": "value"}\n```'
        cleaned = preprocess_json(raw)
        self.assertEqual(json.loads(cleaned), {"key": "value"})

    def test_empty_input(self):
        self.assertEqual(preprocess_json(""), "")
        self.assertEqual(preprocess_json(None), None)

    def test_nested_trailing_commas(self):
        raw = '{"a": {"b": 1,}, "c": [2, 3,],}'
        cleaned = preprocess_json(raw)
        self.assertEqual(json.loads(cleaned), {"a": {"b": 1}, "c": [2, 3]})


class TestSafeJsonLoads(unittest.TestCase):
    def test_normal_json(self):
        result = safe_json_loads('{"a": 1}')
        self.assertEqual(result, {"a": 1})

    def test_trailing_comma(self):
        result = safe_json_loads('{"a": 1,}')
        self.assertEqual(result, {"a": 1})

    def test_empty_input(self):
        result = safe_json_loads("")
        self.assertEqual(result, {})

    def test_default_on_failure(self):
        result = safe_json_loads("not json", default={"fallback": True})
        self.assertEqual(result, {"fallback": True})

    def test_nested_structure(self):
        raw = '{"a": {"b": [1, 2,],}, "c": "test",}'
        result = safe_json_loads(raw)
        self.assertEqual(result, {"a": {"b": [1, 2]}, "c": "test"})


class TestAssembleV3AnalysisData(unittest.TestCase):
    def test_full_structure(self):
        data = assemble_v3_analysis_data()
        self.assertEqual(data["version"], "v3")
        self.assertEqual(data["pipeline_status"], "completed")
        self.assertIn("metadata", data)
        self.assertIn("eligibility", data)
        self.assertIn("scoring", data)
        self.assertIn("packages", data)
        self.assertIn("strategy", data)

    def test_metadata_defaults(self):
        data = assemble_v3_analysis_data()
        meta = data["metadata"]
        self.assertEqual(meta["project_name"], "")
        self.assertEqual(meta["project_code"], "")
        self.assertEqual(meta["budget"]["total"], 0)
        self.assertEqual(meta["package_count"], 0)

    def test_eligibility_defaults(self):
        data = assemble_v3_analysis_data()
        elig = data["eligibility"]
        self.assertEqual(elig["summary"]["total_items"], 0)
        self.assertEqual(elig["qualifications"], [])
        self.assertEqual(elig["disqualifications"], [])

    def test_scoring_defaults(self):
        data = assemble_v3_analysis_data()
        scoring = data["scoring"]
        self.assertEqual(scoring["method"], "")
        self.assertEqual(scoring["total_score"], 0)
        self.assertEqual(scoring["dimensions"], [])

    def test_custom_metadata(self):
        meta = {
            "project_name": "测试项目",
            "project_code": "TEST001",
            "budget": {"total": 1000000},
        }
        data = assemble_v3_analysis_data(metadata=meta)
        self.assertEqual(data["metadata"]["project_name"], "测试项目")
        self.assertEqual(data["metadata"]["project_code"], "TEST001")

    def test_custom_scoring(self):
        scoring = {
            "method": "comprehensive",
            "total_score": 100,
            "dimensions": [
                {"name": "报价", "score": 30, "type": "objective"},
                {"name": "技术", "score": 40, "type": "subjective"},
            ],
        }
        data = assemble_v3_analysis_data(scoring=scoring)
        self.assertEqual(len(data["scoring"]["dimensions"]), 2)
        self.assertEqual(len(data["section_scoring_map"]), 2)

    def test_chinese_encoding(self):
        """验证中文在 JSON 序列化后不乱码。"""
        meta = {"project_name": "成都海关2026年试剂耗材采购项目"}
        data = assemble_v3_analysis_data(metadata=meta)
        json_str = analysis_data_to_json(data)
        self.assertIn("成都海关", json_str)
        self.assertNotIn("\\u", json_str[:json_str.find("成都")] if "成都" in json_str else json_str)


if __name__ == "__main__":
    unittest.main()
