"""单元测试：Phase 1 元数据提取 — 纯规则模式。"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.service_modules.task_pipeline.analysis_v3.phase1_metadata import (
    extract_metadata,
    RULES,
)


class TestMetadataRuleExtract(unittest.TestCase):
    """测试规则提取部分（核心逻辑，不依赖 LLM）。"""

    def test_extract_project_code(self):
        text = "项目编号：ZY20260016ZC-ZJ-A"
        meta = extract_metadata(text)
        self.assertEqual(meta["project_code"]["value"], "ZY20260016ZC-ZJ-A")

    def test_extract_project_code_colon(self):
        text = "项目编号:ZY20260016ZC-ZJ-A"
        meta = extract_metadata(text)
        self.assertEqual(meta["project_code"]["value"], "ZY20260016ZC-ZJ-A")

    def test_extract_project_name(self):
        text = "项目名称：2026年试剂耗材采购项目"
        meta = extract_metadata(text)
        self.assertIn("试剂", meta["project_name"]["value"])

    def test_extract_budget(self):
        text = "采购预算：1015万元，其中：第一包：274万元"
        meta = extract_metadata(text)
        self.assertEqual(meta["budget"]["total"], 10150000.0)

    def test_extract_budget_with_decimal(self):
        text = "预算：274.5万元"
        meta = extract_metadata(text)
        self.assertEqual(meta["budget"]["total"], 2745000.0)

    def test_extract_purchaser(self):
        text = "采购人：四川国际旅行卫生保健中心（成都海关口岸门诊部）"
        meta = extract_metadata(text)
        self.assertIn("四川", meta["purchaser"]["name"])

    def test_extract_agent(self):
        text = "采购代理机构：四川中意招标有限公司"
        meta = extract_metadata(text)
        self.assertIn("中意", meta["agent"]["name"])

    def test_extract_bid_deadline(self):
        text = "投标文件递交的起止时间：2026年4月20日9时30分-10时00分"
        meta = extract_metadata(text)
        self.assertIn("2026年4月20日", meta["key_dates"]["bid_deadline"])

    def test_extract_evaluation_method(self):
        text = "评标办法：综合评分法"
        meta = extract_metadata(text)
        self.assertEqual(meta["evaluation_method"], "综合评分法")

    def test_extract_package_count(self):
        text = "本项目共计9个包，各包拟确定1名中标人"
        meta = extract_metadata(text)
        self.assertEqual(meta["package_count"], 9)

    def test_extract_bid_validity(self):
        text = "投标有效期：提交投标文件的截止之日起90天"
        meta = extract_metadata(text)
        self.assertEqual(meta["key_dates"]["bid_validity_days"], 90)

    def test_extract_performance_security(self):
        text = "履约保证金：金额：各包合同金额的10%。"
        meta = extract_metadata(text)
        self.assertEqual(meta["performance_security_pct"], 10)

    def test_allow_consortium_false(self):
        text = "本项目不允许联合体投标"
        meta = extract_metadata(text)
        self.assertFalse(meta["allow_consortium"])

    def test_empty_text(self):
        meta = extract_metadata("")
        self.assertEqual(meta["project_code"]["value"], "")
        self.assertEqual(meta["budget"]["total"], 0)
        self.assertEqual(meta["package_count"], 0)

    def test_no_match(self):
        meta = extract_metadata("这是一段没有任何元数据的文本")
        self.assertEqual(meta["project_code"]["value"], "")

    def test_multi_rule_same_field(self):
        """同一字段有多条规则，取第一条匹配的。"""
        text = "项目编号：ABC12345678\n项目编号:XYZ98765432"
        meta = extract_metadata(text)
        self.assertIn(meta["project_code"]["value"], ["ABC12345678", "XYZ98765432"])

    def test_full_extraction(self):
        """完整的元数据提取"""
        text = """
            项目编号：ZY20260016ZC-ZJ-A
            项目名称：2026年试剂耗材采购项目
            采购人：成都海关
            采购代理机构：四川中意招标有限公司
            采购预算：1015万元
            投标截止时间：2026年4月20日10时00分
            评标办法：综合评分法
            本项目共计9个包
        """
        meta = extract_metadata(text)
        self.assertEqual(meta["project_code"]["value"], "ZY20260016ZC-ZJ-A")
        self.assertIn("试剂", meta["project_name"])
        self.assertIn("成都海关", meta["purchaser"]["name"])
        self.assertEqual(meta["budget"]["total"], 10150000.0)
        self.assertIn("2026年4月20日", meta["key_dates"]["bid_deadline"])
        self.assertEqual(meta["evaluation_method"], "综合评分法")
        self.assertEqual(meta["package_count"], 9)

    def test_llm_free(self):
        """验证不依赖 LLM，纯规则可用。"""
        text = "项目编号：TEST20260001\n项目名称：测试项目"
        meta = extract_metadata(text)
        self.assertEqual(meta["project_code"]["value"], "TEST20260001")
        self.assertEqual(meta["project_name"]["value"], "测试项目")


if __name__ == "__main__":
    unittest.main()
