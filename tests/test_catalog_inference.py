"""单元测试：目录推断器。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.service_modules.task_pipeline.catalog_inference import (
    infer_skeleton_from_analysis,
    _build_qual_children,
    _build_requirement_children,
)


class TestInferFromAnalysis(unittest.TestCase):
    """从分析数据推断目录测试。"""

    def test_hard_mandate_items(self):
        json_data = {
            "mandate_items": [
                {"title": "投标函", "level": "HARD", "segment_id": "sec_5"},
                {"title": "廉洁承诺书", "level": "HARD", "segment_id": "sec_7"},
            ],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        # 8 章标准骨架
        self.assertEqual(len(skeleton), 8)
        # HARD 项聚合到 auth_and_declare（第2章）
        auth = skeleton[1]
        self.assertIn("授权", auth["title"])
        self.assertGreaterEqual(len(auth.get("children", [])), 2)

    def test_qualifications(self):
        json_data = {
            "eligibility": {
                "qualifications": [
                    {"requirement": "具有独立承担民事责任的能力"},
                    {"requirement": "具有良好的商业信誉"},
                ],
            },
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        qual_node = [n for n in skeleton if "资格" in n["title"]]
        self.assertEqual(len(qual_node), 1)
        self.assertIn("资格", qual_node[0]["title"])
        self.assertEqual(qual_node[0]["fill_strategy"], "QUALIFICATION")

    def test_technical_requirements(self):
        json_data = {
            "technical_requirements": [
                {"requirement": "★试剂盒需在 2-8°C 保存"},
                {"requirement": "需提供 CE 认证"},
            ],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        tech_node = [n for n in skeleton if "技术" in n["title"]]
        self.assertEqual(len(tech_node), 1)
        self.assertEqual(tech_node[0]["fill_strategy"], "KB_FIRST")

    def test_scoring_dimensions(self):
        json_data = {
            "scoring": {
                "total_score": 100,
                "dimensions": [
                    {"name": "技术方案", "score": 40},
                    {"name": "商务响应", "score": 30},
                    {"name": "价格", "score": 30},
                ],
            },
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        score_node = [n for n in skeleton if "评分" in n["title"]]
        self.assertEqual(len(score_node), 1)
        self.assertEqual(len(score_node[0]["children"]), 3)

    def test_products(self):
        json_data = {
            "products": [{"name": "试剂A"}, {"name": "试剂B"}, {"name": "试剂C"}],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        price_node = [n for n in skeleton if "报价" in n["title"]]
        self.assertEqual(len(price_node), 1)
        self.assertIn(len(price_node[0]["children"]), [2, 3])  # 报价函 + 一览表 [+ 明细表]

    def test_bid_type_service(self):
        json_data = {
            "metadata": {"bid_type": "SERVICE"},
            "business_requirements": [],
            "technical_requirements": [],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        service_node = [n for n in skeleton if "售后" in n["title"]]
        self.assertEqual(len(service_node), 1)

    def test_fallback_empty(self):
        skeleton = infer_skeleton_from_analysis({})
        # 空数据返回 8 章骨架（P1），非兜底单章
        self.assertEqual(len(skeleton), 8)
        self.assertEqual(skeleton[0]["title"], "报价部分")

    def test_all_empty_structures(self):
        json_data = {
            "mandate_items": [],
            "eligibility": {},
            "business_requirements": [],
            "technical_requirements": [],
            "scoring": {},
            "products": [],
            "packages": [],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        self.assertEqual(len(skeleton), 8)
        self.assertIn(skeleton[0]["id"], ["quotation"])

    def test_scoring_no_dimensions(self):
        json_data = {
            "mandate_items": [],
            "scoring": {"total_score": 0, "dimensions": []},
            "products": [{"name": "产品A"}],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        titles = [n["title"] for n in skeleton]
        self.assertIn("报价部分", titles)
        self.assertIn("评分标准响应", titles)

    def test_duplicate_category(self):
        """同一类型的章节不应重复添加。"""
        json_data = {
            "mandate_items": [{"title": "投标函", "level": "HARD"}],
            "eligibility": {"qualifications": [{"requirement": "营业执照"}]},
            "technical_requirements": [{"requirement": "技术参数"}],
            "scoring": {"total_score": 100, "dimensions": [{"name": "技术", "score": 40}]},
            "products": [{"name": "产品"}],
        }
        skeleton = infer_skeleton_from_analysis(json_data)
        titles = [n["title"] for n in skeleton]
        # 每个类型只能出现一次
        self.assertEqual(titles.count("技术方案"), 1)
        self.assertEqual(titles.count("评分标准响应"), 1)
        self.assertEqual(titles.count("报价部分"), 1)


class TestBuildChildren(unittest.TestCase):
    def test_qual_children(self):
        quals = [
            {"requirement": "营业执照必须在有效期内"},
            {"requirement": "具有良好的商业信誉和健全的财务会计制度"},
        ]
        children = _build_qual_children(quals)
        self.assertEqual(len(children), 2)
        self.assertIn("营业", children[0]["title"])

    def test_qual_children_limit(self):
        quals = [{"requirement": f"要求 {i}"} for i in range(15)]
        children = _build_qual_children(quals)
        self.assertLessEqual(len(children), 10)

    def test_build_req_children(self):
        reqs = [{"requirement": "提供CE认证"}, {"requirement": "提供ISO认证"}]
        children = _build_requirement_children(reqs, "商务")
        self.assertEqual(len(children), 2)
        self.assertIn("商务", children[0]["title"])

    def test_build_req_from_string(self):
        reqs = ["纯文本要求", "另一条要求"]
        children = _build_requirement_children(reqs, "技术")
        self.assertEqual(len(children), 2)


if __name__ == "__main__":
    unittest.main()
