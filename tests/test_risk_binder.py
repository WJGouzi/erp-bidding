"""单元测试：废标条件 → 生成约束转换器。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.service_modules.task_pipeline.risk_binder import (
    convert_to_guardrails,
    bind_guardrails_to_outline,
    _match_guardrail,
    _iter_outline_nodes,
)


class TestConvertToGuardrails(unittest.TestCase):
    """废标条件到生成约束的转换测试。"""

    def test_star_mandatory_match(self):
        """★号参数应转为 MANDATORY_MATCH。"""
        disqualifications = [
            {"condition": "★技术参数不满足直接废标", "level": "HIGH"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "MANDATORY_MATCH")
        self.assertEqual(guardrails[0]["action"], "VERIFY_OR_LEAVE_BLANK")

    def test_signature_required(self):
        """盖章要求应转为 REQUIRED_SIGNATURE。"""
        disqualifications = [
            {"condition": "投标函未盖章的作废标处理", "level": "HIGH"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "REQUIRED_SIGNATURE")
        self.assertEqual(guardrails[0]["action"], "MARK_AS_PENDING")

    def test_qualification_bind(self):
        """资质要求应转为 BIND_TO_SUBJECT。"""
        disqualifications = [
            {"condition": "资质证书不在有效期内作废标处理", "level": "HIGH"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "BIND_TO_SUBJECT")
        self.assertEqual(guardrails[0]["action"], "REQUIRE_SUBJECT_MATERIAL")

    def test_evidence_required(self):
        """业绩要求应转为 EVIDENCE_REQUIRED。"""
        disqualifications = [
            {"condition": "未提供类似项目业绩合同复印件的不得分", "level": "MEDIUM"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "EVIDENCE_REQUIRED")
        self.assertEqual(guardrails[0]["action"], "SKIP_IF_NO_EVIDENCE")

    def test_format_lock(self):
        """格式要求应转为 FORMAT_LOCK。"""
        disqualifications = [
            {"condition": "投标文件未按格式要求填写的作废标处理", "level": "HIGH"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "FORMAT_LOCK")
        self.assertEqual(guardrails[0]["action"], "TEMPLATE_ONLY")

    def test_multiple_disqualifications(self):
        """多条废标条件应全部转换。"""
        disqualifications = [
            {"condition": "★参数不满足直接废标"},
            {"condition": "未盖章作废标处理"},
            {"condition": "资质过期作废标"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 3)
        types = {g["type"] for g in guardrails}
        self.assertIn("MANDATORY_MATCH", types)
        self.assertIn("REQUIRED_SIGNATURE", types)
        self.assertIn("BIND_TO_SUBJECT", types)

    def test_empty_disqualifications(self):
        guardrails = convert_to_guardrails([])
        self.assertEqual(guardrails, [])

    def test_duplicate_deduplication(self):
        """相同的废标条件应去重。"""
        disqualifications = [
            {"condition": "★参数不满足直接废标"},
            {"condition": "★参数不满足直接废标"},
        ]
        guardrails = convert_to_guardrails(disqualifications)
        self.assertEqual(len(guardrails), 1)

    def test_qualification_star_to_guardrail(self):
        """资格要求中的★项也应转为 MANDATORY_MATCH。"""
        qualifications = [
            {"requirement": "★提供有效期内的营业执照"},
        ]
        guardrails = convert_to_guardrails([], qualifications=qualifications)
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "MANDATORY_MATCH")

    def test_unknown_condition_fallback(self):
        """无匹配关键词的条件应默认 EVIDENCE_REQUIRED。"""
        guardrails = convert_to_guardrails([
            {"condition": "其他法规规定的废标情形"},
        ])
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "EVIDENCE_REQUIRED")

    def test_condition_from_text_field(self):
        """兼容 disq 中 text 字段。"""
        guardrails = convert_to_guardrails([
            {"text": "★标记项为实质性要求，不满足废标"},
        ])
        self.assertEqual(len(guardrails), 1)
        self.assertEqual(guardrails[0]["type"], "MANDATORY_MATCH")


class TestBindGuardrailsToOutline(unittest.TestCase):
    """生成约束绑定到目录的测试。"""

    def setUp(self):
        self.outline = [
            {"title": "一、技术方案", "children": [
                {"title": "（一）技术参数响应", "children": []},
            ]},
            {"title": "二、资格证明文件", "children": []},
            {"title": "三、投标函", "children": []},
            {"title": "四、类似项目业绩", "children": []},
            {"title": "五、售后服务", "children": []},
        ]
        self.guardrails = [
            {"type": "MANDATORY_MATCH", "action": "VERIFY_OR_LEAVE_BLANK",
             "detail": "★技术参数必须逐项响应"},
            {"type": "REQUIRED_SIGNATURE", "action": "MARK_AS_PENDING",
             "detail": "投标函须盖章"},
            {"type": "BIND_TO_SUBJECT", "action": "REQUIRE_SUBJECT_MATERIAL",
             "detail": "营业执照须有效"},
            {"type": "EVIDENCE_REQUIRED", "action": "SKIP_IF_NO_EVIDENCE",
             "detail": "业绩合同复印件"},
        ]

    def test_mandatory_match_binds_to_技术(self):
        result = bind_guardrails_to_outline(self.outline, self.guardrails)
        tech_node = result[0]
        self.assertIn("guardrails", tech_node)
        types = {g["type"] for g in tech_node["guardrails"]}
        self.assertIn("MANDATORY_MATCH", types)

    def test_signature_binds_to_函(self):
        result = bind_guardrails_to_outline(self.outline, self.guardrails)
        letter_node = result[2]  # 投标函
        self.assertIn("guardrails", letter_node)
        types = {g["type"] for g in letter_node["guardrails"]}
        self.assertIn("REQUIRED_SIGNATURE", types)

    def test_subject_binds_to_资格(self):
        result = bind_guardrails_to_outline(self.outline, self.guardrails)
        qual_node = result[1]  # 资格证明文件
        self.assertIn("guardrails", qual_node)
        types = {g["type"] for g in qual_node["guardrails"]}
        self.assertIn("BIND_TO_SUBJECT", types)

    def test_evidence_binds_to_业绩(self):
        result = bind_guardrails_to_outline(self.outline, self.guardrails)
        perf_node = result[3]  # 类似项目业绩
        self.assertIn("guardrails", perf_node)
        types = {g["type"] for g in perf_node["guardrails"]}
        self.assertIn("EVIDENCE_REQUIRED", types)

    def test_no_duplicate_bindings(self):
        """同一约束不应重复绑定到同一节点。"""
        result = bind_guardrails_to_outline(self.outline, self.guardrails * 3)
        for node in _iter_outline_nodes(result):
            if "guardrails" in node:
                # 检查是否有重复的 type+detail
                seen = set()
                for g in node["guardrails"]:
                    key = (g["type"], g.get("detail", ""))
                    self.assertNotIn(key, seen,
                                     f"重复绑定 {key} 到 {node['title']}")
                    seen.add(key)

    def test_child_inherits_nothing(self):
        """子节点不应自动继承父节点的guardrails（需显式绑定）。"""
        result = bind_guardrails_to_outline(self.outline, self.guardrails)
        child = result[0]["children"][0]  # 技术参数响应
        # 由于子节点标题含"参数"，它应该收到 MANDATORY_MATCH
        self.assertIn("guardrails", child)
        child_types = {g["type"] for g in child["guardrails"]}
        # 但不应收到与父节点无关的约束
        self.assertNotIn("REQUIRED_SIGNATURE", child_types)

    def test_empty_guardrails(self):
        result = bind_guardrails_to_outline(self.outline, [])
        for node in _iter_outline_nodes(result):
            self.assertNotIn("guardrails", node)

    def test_no_match_guardrails(self):
        """没有节点匹配关键词时不应出错。"""
        outline = [{"title": "一、综合响应", "children": []}]
        result = bind_guardrails_to_outline(outline, self.guardrails)
        self.assertEqual(len(result), 1)
        self.assertNotIn("guardrails", result[0])


class TestMatchGuardrail(unittest.TestCase):
    """内部 _match_guardrail 函数测试。"""

    def test_star(self):
        result = _match_guardrail("★参数不满足废标")
        self.assertEqual(result["type"], "MANDATORY_MATCH")

    def test_asterisk(self):
        result = _match_guardrail("※标记项废标")
        self.assertEqual(result["type"], "MANDATORY_MATCH")

    def test_盖章(self):
        result = _match_guardrail("未盖章废标")
        self.assertEqual(result["type"], "REQUIRED_SIGNATURE")

    def test_资质(self):
        result = _match_guardrail("资质不符废标")
        self.assertEqual(result["type"], "BIND_TO_SUBJECT")

    def test_业绩(self):
        result = _match_guardrail("业绩不足废标")
        self.assertEqual(result["type"], "EVIDENCE_REQUIRED")

    def test_格式(self):
        result = _match_guardrail("格式错误废标")
        self.assertEqual(result["type"], "FORMAT_LOCK")

    def test_default_evidence(self):
        result = _match_guardrail("其他不可预见情形")
        self.assertEqual(result["type"], "EVIDENCE_REQUIRED")


class TestIterOutlineNodes(unittest.TestCase):
    """目录树遍历器测试。"""

    def test_flat_list(self):
        nodes = list(_iter_outline_nodes([
            {"title": "A", "children": []},
            {"title": "B", "children": []},
        ]))
        self.assertEqual(len(nodes), 2)

    def test_nested_tree(self):
        tree = [
            {"title": "A", "children": [
                {"title": "A1", "children": [
                    {"title": "A1a", "children": []},
                ]},
            ]},
        ]
        nodes = list(_iter_outline_nodes(tree))
        self.assertEqual(len(nodes), 3)
        titles = [n["title"] for n in nodes]
        self.assertEqual(titles, ["A", "A1", "A1a"])

    def test_empty(self):
        nodes = list(_iter_outline_nodes([]))
        self.assertEqual(nodes, [])


if __name__ == "__main__":
    unittest.main()
