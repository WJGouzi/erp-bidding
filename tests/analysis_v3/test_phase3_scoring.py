"""单元测试：Phase 3 得分点拆解 — 纯规则模式，无LLM。"""

import sys
import os
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.service_modules.task_pipeline.analysis_v3.phase3_scoring import (
    parse_scoring_table,
    _find_col_index,
    _extract_number,
    _detect_score_type,
    _extract_sub_dimensions,
    _count_pkg_params,
    _detect_core_products,
    _parse_scoring_from_text,
    _detect_text_tables,
    _detect_scoring_method,
    SCORE_TABLE_HEADERS,
)


class FakeTableBlock:
    """模拟 ContentBlock(type=table)"""
    def __init__(self, headers, rows):
        self.headers = headers
        self.rows = rows
        self.type = "table"
        self.text = ""
        self.level = 0


class TestFindColIndex(unittest.TestCase):
    def test_exact_match(self):
        idx = _find_col_index(["序号", "评分因素", "分值", "评分标准"], ["评分因素", "评分项"])
        self.assertEqual(idx, 1)

    def test_partial_match(self):
        idx = _find_col_index(["评分因素（30分）", "分值", "评分标准"], ["评分因素", "评分项"])
        self.assertEqual(idx, 0)

    def test_no_match(self):
        idx = _find_col_index(["A", "B", "C"], ["X", "Y"])
        self.assertIsNone(idx)


class TestExtractNumber(unittest.TestCase):
    def test_with_unit(self):
        self.assertEqual(_extract_number("30分"), 30.0)

    def test_with_decimal(self):
        self.assertEqual(_extract_number("27.5分"), 27.5)

    def test_plain_number(self):
        self.assertEqual(_extract_number("30"), 30.0)

    def test_no_number(self):
        self.assertEqual(_extract_number("无"), 0)

    def test_number_in_sentence(self):
        self.assertEqual(_extract_number("报价得分30分"), 30.0)


class TestDetectScoreType(unittest.TestCase):
    def test_objective_price(self):
        self.assertEqual(_detect_score_type("报价"), "objective")

    def test_subjective_plan(self):
        self.assertEqual(_detect_score_type("技术方案"), "semi_objective")

    def test_subjective_measure(self):
        self.assertEqual(_detect_score_type("保障措施"), "subjective")

    def test_semi_objective_tech(self):
        self.assertEqual(_detect_score_type("技术参数", "满足得满分"), "semi_objective")

    def test_objective_performance(self):
        self.assertEqual(_detect_score_type("业绩"), "objective")


class TestExtractSubDimensions(unittest.TestCase):
    def test_numbered_sub_dims(self):
        result = _extract_sub_dimensions("1. 方案完整性 2. 可行性 3. 创新性")
        self.assertGreaterEqual(len(result), 2)

    def test_cn_sub_dims(self):
        result = _extract_sub_dimensions("（一）方案完整性（二）可行性")
        self.assertGreaterEqual(len(result), 1)

    def test_empty(self):
        self.assertEqual(_extract_sub_dimensions(""), [])


class TestParseScoringTable(unittest.TestCase):
    def test_standard_table(self):
        block = FakeTableBlock(
            headers=["评分因素", "分值", "评分标准"],
            rows=[
                ["报价", "30", "最低价得满分"],
                ["技术参数", "30", "满足得满分，偏离扣分"],
                ["业绩", "8", "每提供一个得2分"],
                ["配送方案", "32", "方案完整性评分"],
            ],
        )
        dims = parse_scoring_table(block)
        self.assertEqual(len(dims), 4)
        self.assertEqual(dims[0]["name"], "报价")
        self.assertEqual(dims[0]["score"], 30)
        self.assertEqual(dims[0]["type"], "objective")

    def test_variable_header_order(self):
        block = FakeTableBlock(
            headers=["序号", "评审项目", "标准分值", "评审细则"],
            rows=[
                ["1", "价格", "30", "低价优先"],
                ["2", "技术", "40", "满足要求"],
            ],
        )
        dims = parse_scoring_table(block)
        self.assertEqual(len(dims), 2)
        self.assertEqual(dims[0]["name"], "价格")
        self.assertEqual(dims[0]["score"], 30)

    def test_no_matching_table(self):
        block = FakeTableBlock(
            headers=["姓名", "年龄", "性别"],
            rows=[["张三", "30", "男"]],
        )
        dims = parse_scoring_table(block)
        # 没有评分相关表头，应返回空列表或降级
        self.assertIsInstance(dims, list)

    def test_empty_table(self):
        block = FakeTableBlock(headers=[], rows=[])
        dims = parse_scoring_table(block)
        self.assertEqual(dims, [])


class TestParseScoringFromText(unittest.TestCase):
    def test_bracket_format(self):
        text = "报价得分（30分） 技术方案（40分） 业绩（30分）"
        dims = _parse_scoring_from_text(text)
        self.assertGreaterEqual(len(dims), 2)

    def test_colon_format(self):
        text = "报价：30分 技术：40分"
        dims = _parse_scoring_from_text(text)
        self.assertGreaterEqual(len(dims), 1)


class TestDetectTextTables(unittest.TestCase):
    def test_pipe_table(self):
        text = "| 评分因素 | 分值 | 评分标准 |\n| 报价 | 30 | 低价优先 |\n| 技术 | 40 | 满足要求 |"
        tables = _detect_text_tables(text)
        self.assertGreaterEqual(len(tables), 1)

    def test_no_table(self):
        text = "这是一段普通文本\n没有表格结构"
        tables = _detect_text_tables(text)
        self.assertEqual(len(tables), 0)


class TestCountPkgParams(unittest.TestCase):
    def test_count_stars(self):
        text = "★参数一\n★参数二\n▲参数三\n普通参数"
        starred, important, general = _count_pkg_params(text)
        self.assertEqual(starred, 2)
        self.assertEqual(important, 1)


class TestDetectScoringMethod(unittest.TestCase):
    def test_comprehensive(self):
        self.assertEqual(_detect_scoring_method("综合评分法"), "comprehensive")

    def test_lowest_price(self):
        self.assertEqual(_detect_scoring_method("最低评标价法"), "lowest_price")


if __name__ == "__main__":
    unittest.main()
