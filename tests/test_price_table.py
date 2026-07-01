"""单元测试：报价一览表表格修复 — 表格原样复用 + 数据正确映射

场景：
1. 表格应该使用招标文件原始的表头和行数据
2. 产品数据应正确映射到对应列
3. 不应出现"待填写"、"★计量单位"等列名泄漏为数据
4. 空单元格应保持空白，不应填充错误数据

运行：
    cd ... && python3 -m unittest tests.test_price_table -v
"""

import sys, os, json, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.service_modules.task_pipeline.helpers import (
    _extract_table_data_from_analysis,
    PRODUCT_COLUMN_MAP,
)


class TestPriceTableDataMapping(unittest.TestCase):
    """测试报价一览表的数据映射正确性。"""

    def setUp(self):
        # 模拟招标文件原始表格结构
        self.raw_table = {
            "headers": ["序号", "采购产品名称", "★规格参数", "★数量", "★计量单位", "★单价最高限价", "备注"],
            "rows": [
                ["1", "丙型肝炎病毒抗体检测试剂盒（化学发光法）", "", "1", "盒", "", ""],
                ["2", "人类免疫缺陷病毒抗原抗体检测试剂盒（化学发光法）", "", "2", "盒", "", ""],
                ["3", "梅毒螺旋体抗体检测试剂盒（化学发光法）", "", "3", "盒", "", ""],
                ["4", "乙型肝炎病毒表面抗原检测试剂盒（化学发光法）", "", "", "盒", "", ""],
                ["5", "", "", "", "", "", ""],  # 完全空行
            ],
        }

        self.analysis_context = {
            "_raw_product_tables": [self.raw_table],
            "_raw_product_lists": [],
            "bidder_notice": {},
            "qualification_review": {},
            "technical_requirements": "",
            "business_requirements": "",
            "requirements": "",
        }

    def test_original_table_headers_preserved(self):
        """表头应与招标文件原始表头一致。"""
        result = _extract_table_data_from_analysis("报价一览表", self.analysis_context, {})
        self.assertIsNotNone(result)
        # 验证行数
        self.assertGreater(len(result), 0, "应有数据行")
        # 检查第一行数据的产品名是否正确
        first_row = result[0] if result else []
        self.assertIn("丙型肝炎病毒", str(first_row), "产品名称应来自原始表格")

    def test_no_placeholder_in_data(self):
        """数据中不应出现"待填写"、"★计量单位"等占位/列名泄漏。"""
        result = _extract_table_data_from_analysis("报价一览表", self.analysis_context, {})
        all_text = " ".join(" ".join(row) for row in result)
        self.assertNotIn("待填写", all_text, "数据中不应出现 '待填写'")
        self.assertNotIn("★计量单位", all_text, "数据中不应泄漏列名 '★计量单位'")
        self.assertNotIn("★规格参数", all_text, "数据中不应泄漏列名 '★规格参数'")

    def test_empty_name_not_filled(self):
        """名称为空的产品应保持空，不填充错误数据。"""
        result = _extract_table_data_from_analysis("报价一览表", self.analysis_context, {})
        # 第5行产品名为空
        row5 = result[4] if len(result) > 4 else []
        self.assertNotIn("待填写", str(row5), "空产品名不应被填充为 '待填写'")

    def test_data_in_correct_columns(self):
        """数据应在正确的列位置，不能错位。"""
        result = _extract_table_data_from_analysis("报价一览表", self.analysis_context, {})
        if len(result) >= 2:
            row2 = result[1]
            # 检查行数据是否与原始表格一致
            row_text = " ".join(row2)
            self.assertIn("人类免疫缺陷病毒", row_text, "产品名应在第一列")
            self.assertIn("2", " ".join(row2), "数量应在对应列")

    def test_raw_product_tables_empty_fallback(self):
        """当 _raw_product_tables 为空时，不应崩溃。"""
        ctx = dict(self.analysis_context)
        ctx["_raw_product_tables"] = []
        result = _extract_table_data_from_analysis("报价一览表", ctx, {})
        self.assertIsInstance(result, list)

    def test_column_mapping_correct(self):
        """列名映射应正确匹配到标准字段。"""
        # 验证 "采购产品名称" 映射到 "name"
        mapping = {}
        for i, h in enumerate(self.raw_table["headers"]):
            for std_field, candidates in PRODUCT_COLUMN_MAP.items():
                if any(c in h for c in candidates):
                    mapping[h] = std_field
                    break
        self.assertEqual(mapping.get("采购产品名称"), "name", "采购产品名称应映射到 name")
        self.assertEqual(mapping.get("★数量"), "qty", "★数量应映射到 qty")
        self.assertEqual(mapping.get("★计量单位"), "unit", "★计量单位应映射到 unit")
        self.assertEqual(mapping.get("★单价最高限价"), "unit_price", "★单价最高限价应映射到 unit_price")


class TestProductColumnMap(unittest.TestCase):
    """PRODUCT_COLUMN_MAP 字段覆盖测试。"""

    def test_all_common_headers_mapped(self):
        """常见的招标文件表头都应被映射。"""
        test_headers = [
            ("采购产品名称", "name"),
            ("产品名称", "name"),
            ("★规格参数", "spec"),
            ("技术参数与性能指标", "spec"),
            ("★数量", "qty"),
            ("数量", "qty"),
            ("★计量单位", "unit"),
            ("计量单位", "unit"),
            ("★单价最高限价", "unit_price"),
            ("单价最高限价", "unit_price"),
            ("单价", "unit_price"),
        ]
        for header, expected_std in test_headers:
            mapped = None
            for std_field, candidates in PRODUCT_COLUMN_MAP.items():
                if any(c in header for c in candidates):
                    mapped = std_field
                    break
            self.assertEqual(mapped, expected_std,
                             f"'{header}' 应映射到 '{expected_std}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
"""单元测试：表格原样复用 + 产品库填空引擎

测试新函数:
  _fill_table_from_original()
  _fetch_product_data()
  _generate_table_content() 新路径

运行：
    source .venv/bin/activate && python3 -m unittest tests.test_price_table -v
"""

import sys, os, json, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 需要在 import 前设置 TESTING 标志
os.environ["FLASK_ENV"] = "TESTING"

from unittest.mock import patch, MagicMock
from app.service_modules.task_pipeline.helpers import (
    _fill_table_from_original,
    _generate_table_content,
    _fetch_product_data,
    _TABLE_MARKER_PREFIX,
    PRODUCT_COLUMN_MAP,
    PRODUCT_FIELD_TO_COLUMN,
)


class TestFillTableFromOriginal(unittest.TestCase):
    """测试 _fill_table_from_original — 原始表格填空引擎。"""

    def setUp(self):
        self.headers = ["产品名称", "品牌", "规格型号", "数量", "单价", "备注"]
        self.rows = [
            ["A试剂盒", "", "10ml", "100", "50", ""],
            ["B检测试剂", "", "20ml", "200", "80", ""],
            ["C培养基", "XX品牌", "500ml", "50", "30", "常温保存"],
        ]
        # 第3行品牌非空，不应被覆盖
        # 第4行名称为空，不应触发查询

    @patch("app.service_modules.task_pipeline.helpers._fetch_product_data")
    def test_fill_empty_brand(self, mock_fetch):
        """品牌列为空时，应从产品库填充品牌信息。"""
        mock_fetch.return_value = {
            "A试剂盒": {"brand": "XX生物", "specAndModel": "10ml"},
            "B检测试剂": {"brand": "YY科技", "specAndModel": "20ml"},
        }
        result = _fill_table_from_original(self.headers, self.rows[:2])
        # A试剂盒品牌列应从空变为"XX生物"
        self.assertEqual(result[0][1], "XX生物")
        # B检测试剂品牌列应从空变为"YY科技"
        self.assertEqual(result[1][1], "YY科技")
        # specAndModel 匹配到了"规格型号"列，但已非空，不应覆盖
        self.assertEqual(result[0][2], "10ml")  # 已有内容，但匹配结果相同

    @patch("app.service_modules.task_pipeline.helpers._fetch_product_data")
    def test_non_empty_cell_not_overwritten(self, mock_fetch):
        """已有内容的单元格不应被覆盖。"""
        mock_fetch.return_value = {
            "C培养基": {"brand": "新品牌", "specAndModel": "1000ml"},
        }
        result = _fill_table_from_original(self.headers, [self.rows[2]])
        # 品牌已为"XX品牌"，不应被覆盖为"新品牌"
        self.assertEqual(result[0][1], "XX品牌")
        # 规格型号已为"500ml"，不应被覆盖
        self.assertEqual(result[0][2], "500ml")

    @patch("app.service_modules.task_pipeline.helpers._fetch_product_data")
    def test_no_product_data_keeps_original(self, mock_fetch):
        """产品库没找到时，原样返回。"""
        mock_fetch.return_value = {}
        result = _fill_table_from_original(self.headers, self.rows)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0][0], "A试剂盒")
        self.assertEqual(result[0][1], "")  # 品牌仍为空

    @patch("app.service_modules.task_pipeline.helpers._fetch_product_data")
    def test_no_name_column_returns_original(self, mock_fetch):
        """找不到产品名列时，不做填充直接返回原行。"""
        headers_no_name = ["序号", "参数", "值"]
        rows_no_name = [["1", "abc", "123"]]
        result = _fill_table_from_original(headers_no_name, rows_no_name)
        mock_fetch.assert_not_called()  # 不应触发产品库查询
        self.assertEqual(result, rows_no_name)

    def test_empty_inputs(self):
        """空输入不崩溃。"""
        self.assertEqual(_fill_table_from_original([], []), [])
        self.assertEqual(_fill_table_from_original([], [["a"]]), [["a"]])
        self.assertEqual(_fill_table_from_original(None, None), [])


class TestGenerateTableContentNewPath(unittest.TestCase):
    """测试 _generate_table_content 新路径（原始表复用）。"""

    def setUp(self):
        self.analysis_context = {
            "_raw_product_tables": [{
                "headers": ["产品名称", "品牌", "★规格参数", "数量"],
                "rows": [
                    ["A试剂盒", "", "10ml", "100"],
                    ["B检测试剂", "", "20ml", "200"],
                ],
            }],
            "_raw_product_lists": [],
            "bidder_notice": {},
            "qualification_review": {},
            "technical_requirements": "",
            "business_requirements": "",
            "requirements": "",
        }

    @patch("app.service_modules.task_pipeline.helpers._fetch_product_data")
    def test_uses_original_headers(self, mock_fetch):
        """报价表应使用原始表头而非硬编码表头。"""
        mock_fetch.return_value = {
            "A试剂盒": {"brand": "XX生物"},
            "B检测试剂": {"brand": "YY科技"},
        }
        result = _generate_table_content(
            "报价一览表", "项目总报价", self.analysis_context, {}
        )
        # 结果应以 _TABLE_MARKER_PREFIX 开头
        self.assertTrue(result.startswith(_TABLE_MARKER_PREFIX))
        # 表头行应为原始表头
        lines = result.split("\n")
        # 表头行在 lines[1]（lines[0] 是 marker）
        header_line = lines[1]
        # 表头应该是原始表头
        self.assertIn("产品名称", header_line, "表头应包含原始列名'产品名称'")
        self.assertIn("品牌", header_line, "表头应包含原始列名'品牌'")
        # 不应出现硬编码的"标的名称"、"总价"等
        self.assertNotIn("标的名称", header_line)
        self.assertNotIn("总价", header_line)

    @patch("app.service_modules.task_pipeline.helpers._fetch_product_data")
    def test_product_data_filled(self, mock_fetch):
        """品牌等空列应被产品库数据填充。"""
        mock_fetch.return_value = {
            "A试剂盒": {"brand": "XX生物"},
            "B检测试剂": {"brand": "YY科技"},
        }
        result = _generate_table_content(
            "报价一览表", "项目总报价", self.analysis_context, {}
        )
        lines = result.split("\n")
        # 数据行1: 品牌列应为"XX生物"
        self.assertIn("XX生物", lines[2])
        # 数据行2: 品牌列应为"YY科技"
        self.assertIn("YY科技", lines[3])
        # 数据行1: 已有内容的列不变
        self.assertIn("10ml", lines[2])

    def test_no_raw_tables_falls_back(self):
        """_raw_product_tables 为空时降级到旧逻辑。"""
        ctx = dict(self.analysis_context)
        ctx["_raw_product_tables"] = []
        result = _generate_table_content(
            "报价一览表", "项目总报价", ctx, {}
        )
        # 旧路径不会崩溃，返回有效结果
        self.assertTrue(result.startswith(_TABLE_MARKER_PREFIX))
        lines = result.split("\n")
        # 旧路径表头为硬编码
        self.assertTrue(any("标的名称" in line for line in lines) or any("序号" in line for line in lines))

    def test_non_price_table_uses_old_path(self):
        """非报价表仍走旧路径。"""
        result = _generate_table_content(
            "商务要求偏离表", "商务偏离说明", self.analysis_context, {}
        )
        self.assertTrue(result.startswith(_TABLE_MARKER_PREFIX))
        lines = result.split("\n")
        # 旧路径表头包含"商务条款"
        self.assertTrue(any("商务条款" in line for line in lines))


class TestProductFieldMapping(unittest.TestCase):
    """PRODUCT_FIELD_TO_COLUMN 映射验证。"""

    def test_common_headers_mapped(self):
        """常见招标文件列名应被正确映射到产品库字段。"""
        test_cases = [
            ("品牌", "brand"),
            ("生产厂家", "brand"),
            ("厂家", "brand"),
            ("规格", "specAndModel"),
            ("★规格参数", "specAndModel"),
            ("规格型号", "specAndModel"),
            ("单位", "unit"),
            ("★计量单位", "unit"),
            ("货号", "articleNo"),
            ("储存条件", "storageCondition"),
            ("保质期", "qualityPeriod"),
            ("注册证号", "registrationCertificateNo"),
        ]
        for header, expected_field in test_cases:
            matched = None
            for product_field, col_candidates in PRODUCT_FIELD_TO_COLUMN.items():
                if any(c in header for c in col_candidates):
                    matched = product_field
                    break
            self.assertEqual(matched, expected_field,
                             f"'{header}' 应映射到 '{expected_field}'")

    def test_product_column_map_complete(self):
        """PRODUCT_COLUMN_MAP 应覆盖所有常见列名。"""
        required_keys = {"name", "spec", "brand", "qty", "unit", "unit_price"}
        present_keys = set(PRODUCT_COLUMN_MAP.keys())
        missing = required_keys - present_keys
        self.assertFalse(missing, f"PRODUCT_COLUMN_MAP 缺少必要字段: {missing}")


class TestFetchProductData(unittest.TestCase):
    """测试 _fetch_product_data 的适配器调用。"""

    def setUp(self):
        from flask import Flask
        app = Flask(__name__)
        app.config["CHROMA_HOST"] = "localhost"
        app.config["PRODUCT_CHROMA_TENANT"] = "erp"
        app.config["PRODUCT_CHROMA_DATABASE"] = "erp"
        app.config["PRODUCT_CHROMA_COLLECTION"] = "product"
        self.app_ctx = app.app_context()
        self.app_ctx.push()

    def tearDown(self):
        self.app_ctx.pop()

    def _mock_config_get(self, key, default=None):
        cfg = {
            "CHROMA_HOST": "localhost",
            "PRODUCT_CHROMA_TENANT": "erp",
            "PRODUCT_CHROMA_DATABASE": "erp",
            "PRODUCT_CHROMA_COLLECTION": "product",
        }
        return cfg.get(key, default)

    def test_empty_names_returns_empty(self):
        """空产品名列表应返回空。"""
        result = _fetch_product_data([])
        self.assertEqual(result, {})

    def test_query_object_called(self):
        """每个产品名应调用一次 query_objects。"""
        with patch("app.service_modules.task_pipeline.helpers.ChromaAdapter") as mock_adapter_cls:
            mock_instance = MagicMock()
            mock_adapter_cls.return_value = mock_instance
            mock_instance.query_objects.return_value = {
                "matches": [{
                    "object_json": json.dumps({
                        "productName": "A试剂盒",
                        "brand": "XX生物",
                        "specAndModel": "10ml",
                        "manufacturer": "XX科技有限公司",
                    }),
                    "distance": 0.85,
                }]
            }
            
            result = _fetch_product_data(["A试剂盒", "B检测试剂"], adapter=mock_instance)
            mock_instance.query_objects.assert_any_call(
                "product", query_text="A试剂盒", top_k=1
            )
            self.assertIn("A试剂盒", result)
            self.assertEqual(result["A试剂盒"].get("brand"), "XX生物")

    def test_empty_object_json_skipped(self):
        """object_json 为空时应跳过。"""
        mock_instance = MagicMock()
        mock_instance.query_objects.return_value = {
            "matches": [{"object_json": "", "distance": 0.9}]
        }
        result = _fetch_product_data(["A试剂盒"], adapter=mock_instance)
        self.assertEqual(result, {})

    def test_hyphen_value_filtered(self):
        """值为 '-' 的占位符应被过滤。"""
        mock_instance = MagicMock()
        mock_instance.query_objects.return_value = {
            "matches": [{
                "object_json": json.dumps({
                    "productName": "A试剂盒",
                    "brand": "-",
                    "manufacturer": "",
                }),
                "distance": 0.9,
            }]
        }
        result = _fetch_product_data(["A试剂盒"], adapter=mock_instance)
        self.assertIn("A试剂盒", result)
        self.assertNotIn("brand", result.get("A试剂盒", {}))
        self.assertNotIn("manufacturer", result.get("A试剂盒", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
