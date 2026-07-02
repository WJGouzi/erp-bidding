"""单元测试：LLM 组装器。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.service_modules.task_pipeline.analysis_v3.assembler import (
    assemble,
    _basic_merge,
    _build_associations,
    _build_segment_binding,
)


class TestAssemble(unittest.TestCase):
    """组装器主入口测试。"""

    def test_empty_segments(self):
        result = assemble([])
        self.assertIn("metadata", result)
        self.assertIn("mandate_items", result)
        self.assertIn("eligibility", result)
        self.assertEqual(result["mandate_items"], [])

    def test_basic_merge_metadata(self):
        segments = [
            {
                "segment_id": "sec_1",
                "title": "投标人须知",
                "page_range": [1, 5],
                "metadata": {"project_name": "2026年试剂采购项目", "project_code": "CG2025-001"},
                "mandate_level": None,
                "eligibility": {},
                "scoring": {},
                "raw_excerpt": "项目名称：2026年试剂采购项目",
            },
        ]
        result = assemble(segments)
        self.assertEqual(result["metadata"]["project_name"]["value"], "2026年试剂采购项目")
        self.assertEqual(result["metadata"]["project_name"]["source_segment_ids"], ["sec_1"])

    def test_merge_mandate_items(self):
        segments = [
            {
                "segment_id": "sec_5",
                "title": "投标函",
                "mandate_level": {"level": "HARD", "reason": "精确标题匹配", "source": "rule:exact_title"},
                "metadata": {},
                "eligibility": {},
                "scoring": {},
                "raw_excerpt": "投标函",
            },
            {
                "segment_id": "sec_7",
                "title": "技术方案",
                "mandate_level": {"level": "FREE", "reason": "自由内容", "source": "default"},
                "metadata": {},
                "eligibility": {},
                "scoring": {},
                "raw_excerpt": "",
            },
        ]
        result = assemble(segments)
        self.assertEqual(len(result["mandate_items"]), 1)
        self.assertEqual(result["mandate_items"][0]["title"], "投标函")
        self.assertEqual(result["mandate_items"][0]["segment_id"], "sec_5")

    def test_merge_qualifications(self):
        segments = [
            {
                "segment_id": "sec_3",
                "title": "资格要求",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {
                    "qualifications": [
                        {"requirement": "具有独立承担民事责任的能力"},
                        {"requirement": "具有良好的商业信誉"},
                    ],
                },
                "scoring": {},
                "raw_excerpt": "",
            },
            {
                "segment_id": "sec_4",
                "title": "资格要求续",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {
                    "qualifications": [
                        {"requirement": "具有履行合同所必需的设备和专业技术能力"},
                    ],
                },
                "scoring": {},
                "raw_excerpt": "",
            },
        ]
        result = assemble(segments)
        quals = result["eligibility"]["qualifications"]
        self.assertEqual(len(quals), 3)

    def test_merge_disqualifications(self):
        segments = [
            {
                "segment_id": "sec_6",
                "title": "废标条件",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {
                    "disqualifications": [
                        {"condition": "★技术参数不满足直接废标", "level": "HIGH"},
                    ],
                },
                "scoring": {},
                "raw_excerpt": "",
            },
            {
                "segment_id": "sec_8",
                "title": "其他废标",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {
                    "disqualifications": [
                        {"condition": "未盖章作废标处理", "level": "HIGH"},
                    ],
                },
                "scoring": {},
                "raw_excerpt": "",
            },
        ]
        result = assemble(segments)
        disqs = result["eligibility"]["disqualifications"]
        self.assertEqual(len(disqs), 2)

    def test_merge_scoring(self):
        segments = [
            {
                "segment_id": "sec_9",
                "title": "评分标准",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {},
                "scoring": {
                    "method": "综合评分法",
                    "total_score": 100,
                    "dimensions": [
                        {"name": "技术方案", "score": 40, "criteria": "方案完整可行"},
                        {"name": "价格", "score": 30, "criteria": "最低价优先"},
                    ],
                },
                "raw_excerpt": "",
            },
        ]
        result = assemble(segments)
        self.assertEqual(result["scoring"]["method"], "综合评分法")
        self.assertEqual(result["scoring"]["total_score"], 100)
        self.assertEqual(len(result["scoring"]["dimensions"]), 2)

    def test_scoring_merge_across_segments(self):
        """评分维度应从多个段合并，不重复。"""
        segments = [
            {
                "segment_id": "sec_9",
                "title": "商务评分",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {},
                "scoring": {
                    "dimensions": [{"name": "商务响应", "score": 30}],
                },
                "raw_excerpt": "",
            },
            {
                "segment_id": "sec_10",
                "title": "技术评分",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {},
                "scoring": {
                    "dimensions": [{"name": "技术方案", "score": 40}],
                },
                "raw_excerpt": "",
            },
        ]
        result = assemble(segments)
        self.assertEqual(len(result["scoring"]["dimensions"]), 2)
        names = [d["name"] for d in result["scoring"]["dimensions"]]
        self.assertIn("商务响应", names)
        self.assertIn("技术方案", names)

    def test_deduplication(self):
        """相同内容应去重。"""
        segments = [
            {
                "segment_id": "sec_1",
                "title": "资格要求",
                "metadata": {},
                "mandate_level": None,
                "eligibility": {
                    "qualifications": [{"requirement": "营业执照"}, {"requirement": "营业执照"}],
                },
                "scoring": {},
                "raw_excerpt": "",
            },
        ]
        result = assemble(segments)
        self.assertEqual(len(result["eligibility"]["qualifications"]), 1)


class TestBuildAssociations(unittest.TestCase):
    """废标↔资格关联测试。"""

    def test_keyword_overlap(self):
        result = {
            "eligibility": {
                "qualifications": [
                    {"requirement": "提供有效期内的营业执照"},
                    {"requirement": "具有良好的商业信誉"},
                ],
                "disqualifications": [
                    {"condition": "营业执照过期或无效的作废标处理"},
                ],
            },
        }
        result = _build_associations(result)
        bindings = result["eligibility"].get("disqualification_bindings", [])
        self.assertEqual(len(bindings), 1)
        self.assertIn("营业执照", bindings[0]["disqualification"])

    def test_no_overlap(self):
        result = {
            "eligibility": {
                "qualifications": [
                    {"requirement": "具有独立承担民事责任的能力"},
                ],
                "disqualifications": [
                    {"condition": "报价超过预算废标"},
                ],
            },
        }
        result = _build_associations(result)
        bindings = result["eligibility"].get("disqualification_bindings", [])
        self.assertEqual(bindings, [])


class TestBuildSegmentBinding(unittest.TestCase):
    """来源绑定索引测试。"""

    def test_binding_contains_keys(self):
        result = {
            "metadata": {
                "project_name": {"value": "项目", "source_segment_ids": ["sec_1"]},
            },
            "mandate_items": [
                {"title": "投标函", "segment_id": "sec_5"},
            ],
            "eligibility": {
                "qualifications": [
                    {"requirement": "营业执照", "source_segment_ids": ["sec_3"]},
                ],
                "disqualifications": [],
            },
            "scoring": {"method": "", "total_score": 0, "dimensions": []},
            "business_requirements": [],
            "technical_requirements": [],
            "products": [],
            "packages": [],
        }
        segments = [{"segment_id": "sec_1"}, {"segment_id": "sec_3"}, {"segment_id": "sec_5"}]
        binding = _build_segment_binding(result, segments)
        self.assertIn("metadata.project_name", binding)
        self.assertIn("mandate_items[0]", binding)
        self.assertIn("eligibility.qualifications[0]", binding)
        self.assertEqual(binding["metadata.project_name"], ["sec_1"])
        self.assertEqual(binding["mandate_items[0]"], ["sec_5"])


if __name__ == "__main__":
    unittest.main()
