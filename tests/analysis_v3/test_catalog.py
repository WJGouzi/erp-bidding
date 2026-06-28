"""单元测试：catalog 模块 — 包过滤、确认项分类、动态目录结构推断。"""

import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch, Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ── 测试目标函数（纯逻辑，无 Flask/SQLAlchemy 依赖） ──
from app.service_modules.task_pipeline.catalog import (
    _build_catalog_description,
    _build_numbered_children,
    _get_filtered_analysis_data,
    _classify_check_items,
    _build_bid_letter_section,
    _build_price_section,
    _build_authorization_section,
    _build_qualification_section,
    _build_compliance_section,
    _count_package_items,
    _build_tech_section,
    _build_business_section,
    _build_scoring_section,
    _build_service_section,
    _build_performance_section,
    _build_other_section,
    _should_fallback_to_legacy,
)


# ═══════════════════════════════════════════════
# _build_catalog_description
# ═══════════════════════════════════════════════

class TestBuildCatalogDescription(unittest.TestCase):
    """测试目录说明摘要裁剪。"""

    def test_normal_text(self):
        result = _build_catalog_description("这是一段正常的描述文本", "fallback", max_length=100)
        self.assertEqual(result, "这是一段正常的描述文本")

    def test_empty_text_uses_fallback(self):
        result = _build_catalog_description("", "fallback text", max_length=100)
        self.assertEqual(result, "fallback text")

    def test_none_text_uses_fallback(self):
        result = _build_catalog_description(None, "fallback text", max_length=100)
        self.assertEqual(result, "fallback text")

    def test_truncation(self):
        long_text = "A" * 200
        result = _build_catalog_description(long_text, "", max_length=50)
        self.assertEqual(len(result), 50)

    def test_multiline_collapse(self):
        result = _build_catalog_description("第一行\n第二行\n第三行", "", max_length=100)
        self.assertEqual(result, "第一行 第二行 第三行")


# ═══════════════════════════════════════════════
# _build_numbered_children
# ═══════════════════════════════════════════════

class TestBuildNumberedChildren(unittest.TestCase):
    """测试子章节编号生成。"""

    def test_basic_numbering(self):
        items = [
            {"title": "项目概况", "description": "项目描述"},
            {"title": "项目基础信息", "description": "基础信息"},
        ]
        result = _build_numbered_children(items)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "（一）项目概况")
        self.assertEqual(result[1]["title"], "（二）项目基础信息")

    def test_skip_empty_title(self):
        items = [
            {"title": "", "description": "描述"},
            {"title": "有效标题", "description": "有效描述"},
        ]
        result = _build_numbered_children(items)
        self.assertEqual(len(result), 1)
        # 注意：编号使用原始列表索引，跳过的条目仍消耗编号
        self.assertEqual(result[0]["title"], "（二）有效标题")

    def test_skip_empty_description(self):
        items = [
            {"title": "标题", "description": ""},
        ]
        result = _build_numbered_children(items)
        self.assertEqual(len(result), 0)

    def test_more_than_8_items(self):
        items = [{"title": f"项{i}", "description": f"描述{i}"} for i in range(10)]
        result = _build_numbered_children(items)
        self.assertEqual(len(result), 10)
        self.assertEqual(result[8]["title"], "（9）项8")


# ═══════════════════════════════════════════════
# _get_filtered_analysis_data
# ═══════════════════════════════════════════════

class FakeAnalysisResult:
    """模拟 BiddingAnalysisResult，避免依赖数据库。"""
    def __init__(self, data_dict):
        self._data = data_dict
    def safe_analysis_data(self):
        return self._data


class TestGetFilteredAnalysisData(unittest.TestCase):
    """测试按包号过滤 analysis_data。"""

    def test_none_result_returns_empty(self):
        result = _get_filtered_analysis_data(None, "1")
        self.assertEqual(result, {})

    def test_empty_data_returns_empty(self):
        result = _get_filtered_analysis_data(FakeAnalysisResult({}), "1")
        self.assertEqual(result, {})

    def test_single_package_no_filter(self):
        data = {"version": "v3", "has_package": False, "packages": [{"package_no": 1}]}
        result = _get_filtered_analysis_data(FakeAnalysisResult(data), "1")
        # has_package=False → 不过滤
        self.assertEqual(len(result["packages"]), 1)

    def test_no_selected_package_no_filter(self):
        data = {"version": "v3", "has_package": True, "packages": [{"package_no": 1}, {"package_no": 2}]}
        result = _get_filtered_analysis_data(FakeAnalysisResult(data), None)
        # selected_package_no is None → 不过滤
        self.assertEqual(len(result["packages"]), 2)

    def test_multi_package_filter_by_no(self):
        data = {"version": "v3", "has_package": True, "packages": [{"package_no": 1, "name": "包1"}, {"package_no": 2, "name": "包2"}]}
        result = _get_filtered_analysis_data(FakeAnalysisResult(data), "2")
        self.assertEqual(len(result["packages"]), 1)
        self.assertEqual(result["packages"][0]["package_no"], 2)
        self.assertEqual(result["package_count"], 1)

    def test_multi_package_filter_int_str(self):
        """包号 int/str 兼容。"""
        data = {"version": "v3", "has_package": True, "packages": [{"package_no": 1}, {"package_no": 2}]}
        result = _get_filtered_analysis_data(FakeAnalysisResult(data), 1)
        self.assertEqual(len(result["packages"]), 1)
        self.assertEqual(result["packages"][0]["package_no"], 1)

    def test_packages_not_a_list(self):
        """防御：packages 字段非 list 时返回原数据。"""
        data = {"version": "v3", "has_package": True, "packages": "not_a_list"}
        result = _get_filtered_analysis_data(FakeAnalysisResult(data), "1")
        self.assertEqual(result, data)

    def test_packages_contains_strings(self):
        """防御：packages 列表中有 str 元素，应跳过。"""
        data = {
            "version": "v3", "has_package": True,
            "packages": [
                {"package_no": 1, "name": "包1"},
                "this is a string, not a dict",
                {"package_no": 2, "name": "包2"},
            ],
        }
        result = _get_filtered_analysis_data(FakeAnalysisResult(data), "1")
        self.assertEqual(len(result["packages"]), 1)
        self.assertEqual(result["packages"][0]["package_no"], 1)


# ═══════════════════════════════════════════════
# _classify_check_items
# ═══════════════════════════════════════════════

class FakeCheckItem:
    """模拟 BiddingCheckItem 实例。"""
    def __init__(self, check_key, confirmed_flag=True, check_label="", check_value=""):
        self.check_key = check_key
        self.confirmed_flag = confirmed_flag
        self.check_label = check_label
        self.check_value = check_value


class TestClassifyCheckItems(unittest.TestCase):
    """测试 check_items 分类。"""

    def test_classify_qualification(self):
        items = [FakeCheckItem("qual_stat_01")]
        result = _classify_check_items(items)
        self.assertEqual(len(result["qualification"]), 1)
        self.assertEqual(len(result["compliance"]), 0)
        self.assertEqual(len(result["disqualification"]), 0)
        self.assertEqual(len(result["scoring"]), 0)

    def test_classify_compliance(self):
        items = [FakeCheckItem("star_01")]
        result = _classify_check_items(items)
        self.assertEqual(len(result["compliance"]), 1)

    def test_classify_disqualification(self):
        items = [FakeCheckItem("disq_01")]
        result = _classify_check_items(items)
        self.assertEqual(len(result["disqualification"]), 1)

    def test_classify_scoring(self):
        items = [FakeCheckItem("score_dim_报价")]
        result = _classify_check_items(items)
        self.assertEqual(len(result["scoring"]), 1)

    def test_classify_empty_list(self):
        result = _classify_check_items([])
        for key in ["qualification", "compliance", "disqualification", "scoring"]:
            self.assertEqual(len(result[key]), 0)

    def test_classify_none(self):
        result = _classify_check_items(None)
        for key in ["qualification", "compliance", "disqualification", "scoring"]:
            self.assertEqual(len(result[key]), 0)

    def test_classify_unknown_key(self):
        items = [FakeCheckItem("unknown_key")]
        result = _classify_check_items(items)
        for key in ["qualification", "compliance", "disqualification", "scoring"]:
            self.assertEqual(len(result[key]), 0)

    def test_classify_mixed(self):
        items = [
            FakeCheckItem("qual_01"),
            FakeCheckItem("star_01"),
            FakeCheckItem("disq_01"),
            FakeCheckItem("score_dim_报价"),
        ]
        result = _classify_check_items(items)
        self.assertEqual(len(result["qualification"]), 1)
        self.assertEqual(len(result["compliance"]), 1)
        self.assertEqual(len(result["disqualification"]), 1)
        self.assertEqual(len(result["scoring"]), 1)


# ═══════════════════════════════════════════════
# Section builders
# ═══════════════════════════════════════════════

class TestBuildBidLetterSection(unittest.TestCase):
    def test_basic(self):
        ctx = {"overview": "测试项目"}
        section = _build_bid_letter_section(ctx)
        self.assertIn("投标函", section["title"])
        self.assertIn("测试项目", section["description"])

    def test_empty_overview(self):
        ctx = {"overview": ""}
        section = _build_bid_letter_section(ctx)
        self.assertIn("投标函", section["title"])


class TestBuildPriceSection(unittest.TestCase):
    def test_with_products(self):
        ctx = {}
        data = {"packages": [{"parameters": {"core_products": ["PCR试剂盒A", "PCR试剂盒B"]}}]}
        section = _build_price_section(ctx, data)
        self.assertIn("报价", section["title"])
        self.assertEqual(len(section["children"]), 2)  # 报价一览表 + 分项报价明细

    def test_no_products(self):
        ctx = {}
        data = {"packages": [{"parameters": {"core_products": []}}]}
        section = _build_price_section(ctx, data)
        self.assertEqual(len(section["children"]), 1)  # 只有报价一览表

    def test_no_packages(self):
        ctx = {}
        data = {"packages": []}
        section = _build_price_section(ctx, data)
        self.assertIn("报价", section["title"])

    def test_package_is_string_skipped(self):
        """防御：packages 中含有 str 元素。"""
        ctx = {}
        data = {"packages": ["invalid_string", {"parameters": {"core_products": ["产品A"]}}]}
        section = _build_price_section(ctx, data)
        self.assertEqual(len(section["children"]), 2)  # 有效数据可正常展开


class TestBuildAuthorizationSection(unittest.TestCase):
    def test_basic(self):
        section = _build_authorization_section()
        self.assertIn("法定代表人", section["title"])


class TestBuildQualificationSection(unittest.TestCase):
    def test_with_items(self):
        classified = {
            "qualification": [
                FakeCheckItem("qual_01", confirmed_flag=True, check_label="营业执照", check_value="需提供营业执照副本"),
                FakeCheckItem("qual_02", confirmed_flag=False, check_label="纳税证明", check_value="需提供纳税记录"),
            ]
        }
        ctx = {}
        section = _build_qualification_section(classified, ctx)
        self.assertIn("资格证明文件", section["title"])
        self.assertEqual(len(section["children"]), 2)
        self.assertIn("营业执照", section["children"][0]["title"])
        self.assertIn("（待准备）", section["children"][1]["title"])  # 未确认标记

    def test_empty(self):
        classified = {"qualification": []}
        section = _build_qualification_section(classified, {})
        self.assertIn("资格证明文件", section["title"])
        self.assertEqual(len(section["children"]), 0)


class TestBuildComplianceSection(unittest.TestCase):
    def test_with_items(self):
        classified = {
            "compliance": [
                FakeCheckItem("star_01", check_label="不接受联合体投标", check_value="本项目不接受联合体"),
            ]
        }
        section = _build_compliance_section(classified)
        self.assertIn("实质性要求", section["title"])
        self.assertEqual(len(section["children"]), 1)
        self.assertIn("★", section["children"][0]["title"])

    def test_empty(self):
        classified = {"compliance": []}
        section = _build_compliance_section(classified)
        self.assertEqual(len(section["children"]), 0)


class TestCountPackageItems(unittest.TestCase):
    def test_count_by_params(self):
        data = {"packages": [{"parameters": {"starred_count": 5, "important_count": 3, "general_count": 2}}]}
        result = _count_package_items(data)
        self.assertEqual(result, 10)

    def test_count_by_core_products_fallback(self):
        data = {"packages": [{"parameters": {"starred_count": 0, "important_count": 0, "general_count": 0, "core_products": ["A", "B", "C"]}}]}
        result = _count_package_items(data)
        self.assertEqual(result, 3)

    def test_no_packages(self):
        result = _count_package_items({"packages": []})
        self.assertEqual(result, 0)

    def test_package_params_not_dict(self):
        """防御：parameters 不是 dict。"""
        data = {"packages": [{"parameters": "not_a_dict"}]}
        result = _count_package_items(data)
        self.assertEqual(result, 0)


class TestBuildTechSection(unittest.TestCase):
    def test_many_items(self):
        ctx = {"technical_requirements": "PCR试剂技术参数要求"}
        data = {"packages": [{"parameters": {"starred_count": 10, "important_count": 5, "general_count": 5}}]}
        section = _build_tech_section(ctx, data)
        self.assertEqual(len(section["children"]), 3)  # 总偏离表 + 详细响应 + 质量保证

    def test_few_items(self):
        ctx = {"technical_requirements": "简单设备"}
        data = {"packages": [{"parameters": {"starred_count": 2, "important_count": 0, "general_count": 0}}]}
        section = _build_tech_section(ctx, data)
        self.assertEqual(len(section["children"]), 1)  # 只有技术参数偏离表

    def test_no_items(self):
        ctx = {}
        data = {"packages": [{"parameters": {}}]}
        section = _build_tech_section(ctx, data)
        self.assertEqual(len(section["children"]), 1)  # 技术方案


class TestBuildScoringSection(unittest.TestCase):
    def test_with_dimensions(self):
        data = {
            "scoring": {
                "method": "comprehensive",
                "total_score": 100,
                "dimensions": [
                    {"name": "报价", "score": 30, "criteria": "最低价得满分"},
                    {"name": "技术", "score": 50, "criteria": "技术参数响应情况"},
                ],
            }
        }
        section = _build_scoring_section(data)
        self.assertIn("评分标准", section["title"])
        self.assertEqual(len(section["children"]), 2)

    def test_no_dimensions(self):
        data = {"scoring": {"dimensions": []}}
        section = _build_scoring_section(data)
        self.assertEqual(len(section["children"]), 0)

    def test_scoring_not_dict(self):
        data = {"scoring": "not_a_dict"}
        section = _build_scoring_section(data)
        self.assertEqual(len(section["children"]), 0)

    def test_dim_contains_string(self):
        """防御：dimensions 中有 str 元素。"""
        data = {
            "scoring": {
                "dimensions": [
                    {"name": "报价", "score": 30},
                    "invalid string dim",
                ],
            }
        }
        section = _build_scoring_section(data)
        self.assertEqual(len(section["children"]), 2)  # 两个都保留
        # 第二个是字符串，用 str(dim) 处理


# ═══════════════════════════════════════════════
# Static section builders
# ═══════════════════════════════════════════════

class TestStaticSections(unittest.TestCase):
    def test_build_service_section(self):
        section = _build_service_section()
        self.assertIn("售后服务", section["title"])
        self.assertEqual(len(section["children"]), 3)

    def test_build_performance_section(self):
        section = _build_performance_section()
        self.assertIn("业绩", section["title"])

    def test_build_other_section(self):
        section = _build_other_section()
        self.assertIn("其他", section["title"])


# ═══════════════════════════════════════════════
# _should_fallback_to_legacy
# ═══════════════════════════════════════════════

class TestShouldFallbackToLegacy(unittest.TestCase):
    def test_no_analysis_result(self):
        self.assertTrue(_should_fallback_to_legacy(None, None, None, None))

    def test_empty_analysis_data(self):
        result = _should_fallback_to_legacy(None, FakeAnalysisResult({}), None, None)
        self.assertTrue(result)

    def test_has_package_no_selection(self):
        """多包项目但未选择包号 → 回退。"""
        data = {"has_package": True, "packages": [{"package_no": 1}]}
        result = _should_fallback_to_legacy(None, FakeAnalysisResult(data), None, None)
        self.assertTrue(result)

    def test_has_package_with_selection(self):
        """多包项目且有包号 → 不回退。"""
        data = {"has_package": True, "packages": [{"package_no": 1}]}
        result = _should_fallback_to_legacy(None, FakeAnalysisResult(data), "1", [])
        self.assertFalse(result)

    def test_single_package_no_selection(self):
        """单包项目无需包号 → 不回退。"""
        data = {"has_package": False, "packages": [{"package_no": 1}]}
        result = _should_fallback_to_legacy(None, FakeAnalysisResult(data), None, [])
        self.assertFalse(result)

    def test_has_package_int_value(self):
        """has_package 为 int 1 也可正确判断。"""
        data = {"has_package": 1, "packages": [{"package_no": 1}]}
        result = _should_fallback_to_legacy(None, FakeAnalysisResult(data), None, [])
        self.assertTrue(result)  # 有包但无选择


# ═══════════════════════════════════════════════
# _build_package_aware_outline (integrated)
# ═══════════════════════════════════════════════

class TestBuildPackageAwareOutline(unittest.TestCase):
    """集成测试：完整的目录结构生成。"""

    @patch("app.service_modules.task_pipeline.catalog._extract_analysis_context")
    def test_full_outline_with_all_data(self, mock_extract):
        """所有数据齐全时生成 11 章。"""
        mock_extract.return_value = {
            "overview": "PCR试剂采购项目",
            "technical_requirements": "26种PCR试剂技术参数",
            "business_requirements": "合同签订后30天内交货",
            "scoring_items": "价格30分,技术50分,商务20分",
        }

        from app.service_modules.task_pipeline.catalog import _build_package_aware_outline

        task = MagicMock()
        analysis_result = MagicMock()
        filtered_data = {
            "packages": [{
                "package_no": 1,
                "parameters": {"starred_count": 15, "important_count": 8, "general_count": 3},
            }],
            "has_package": True,
            "scoring": {
                "dimensions": [
                    {"name": "报价", "score": 30},
                    {"name": "技术", "score": 50},
                    {"name": "商务", "score": 20},
                ],
            },
        }
        classified = {
            "qualification": [FakeCheckItem("qual_01", check_label="营业执照")],
            "compliance": [FakeCheckItem("star_01", check_label="不接受联合体")],
            "disqualification": [],
            "scoring": [],
        }

        outline = _build_package_aware_outline(task, analysis_result, filtered_data, classified)
        self.assertGreaterEqual(len(outline), 10)  # 至少10章

        # 验证章节标题连续性
        titles = [s["title"] for s in outline]
        self.assertIn("投标函", titles[0])
        self.assertIn("报价", titles[1])
        self.assertIn("授权", titles[2])
        self.assertIn("资格证明", titles[3])
        self.assertIn("实质性要求", titles[4])
        self.assertIn("技术参数", titles[5])

    @patch("app.service_modules.task_pipeline.catalog._extract_analysis_context")
    def test_outline_without_check_items(self, mock_extract):
        """无确认项时跳过资格/实质性要求章节。"""
        mock_extract.return_value = {"overview": "", "technical_requirements": "", "business_requirements": ""}

        from app.service_modules.task_pipeline.catalog import _build_package_aware_outline

        task = MagicMock()
        analysis_result = MagicMock()
        filtered_data = {"packages": [], "has_package": False, "scoring": {}}
        classified = {"qualification": [], "compliance": [], "disqualification": [], "scoring": []}

        outline = _build_package_aware_outline(task, analysis_result, filtered_data, classified)
        # 应该是 9 章（无资格证明、实质性要求章节）
        titles = [s["title"] for s in outline]
        self.assertNotIn("资格证明", " ".join(titles))
        self.assertNotIn("实质性要求", " ".join(titles))


# ═══════════════════════════════════════════════
# Mock-based test for _build_constrained_requirement_outline
# ═══════════════════════════════════════════════

class TestBuildConstrainedRequirementOutline(unittest.TestCase):
    @patch("app.service_modules.task_pipeline.catalog._build_dynamic_outline")
    def test_fallback_when_no_data(self, mock_dynamic):
        """无 analysis_data 时回退到旧 3 章结构。"""
        mock_dynamic.return_value = {"outline": [{"title": "旧章节"}]}

        from app.service_modules.task_pipeline.catalog import _build_constrained_requirement_outline

        result = _build_constrained_requirement_outline(
            MagicMock(), None, selected_package_no=None, check_items=None,
        )
        self.assertEqual(len(result["outline"]), 1)
        mock_dynamic.assert_called_once()

    @patch("app.service_modules.task_pipeline.catalog._build_package_aware_outline")
    @patch("app.service_modules.task_pipeline.catalog._extract_analysis_context")
    def test_new_path_with_data(self, mock_extract, mock_pkg_aware):
        """有完整数据时走新路径。"""
        mock_extract.return_value = {"overview": "测试"}
        mock_pkg_aware.return_value = [{"title": "新章节"}, {"title": "新章节2"}]

        from app.service_modules.task_pipeline.catalog import _build_constrained_requirement_outline

        result = _build_constrained_requirement_outline(
            MagicMock(selected_package_no="1"),
            FakeAnalysisResult({
                "version": "v3",
                "has_package": True,
                "packages": [{"package_no": 1}],
                "scoring": {},
            }),
            selected_package_no="1",
            check_items=[FakeCheckItem("qual_01")],
        )
        self.assertEqual(len(result["outline"]), 2)
        mock_pkg_aware.assert_called_once()


if __name__ == "__main__":
    unittest.main()


# ═══════════════════════════════════════════════
# New tests: analysis_data fallback for qualification/scoring
# ═══════════════════════════════════════════════

class TestBuildQualificationSectionWithAnalysisData(unittest.TestCase):
    """测试资格证明文件章节：check_label 为空时从 analysis_data 回退。"""

    def test_empty_label_with_analysis_data_fallback(self):
        """当 check_label 为空时，从 analysis_data 中按 check_key 匹配获取文本。"""
        classified = {
            "qualification": [
                FakeCheckItem("qual_stat_01", confirmed_flag=True, check_label="", check_value=""),
                FakeCheckItem("qual_stat_02", confirmed_flag=False, check_label="", check_value=""),
            ]
        }
        filtered_data = {
            "eligibility": {
                "qualifications": [
                    {"id": "stat_01", "requirement": "具有独立承担民事责任的能力（营业执照）"},
                    {"id": "stat_02", "requirement": "具有良好的商业信誉和健全的财务会计制度"},
                ],
            }
        }
        ctx = {}
        section = _build_qualification_section(classified, ctx, filtered_data)
        self.assertEqual(len(section["children"]), 2)
        # stat_01 应匹配到 requirement 文本
        self.assertIn("营业执照", section["children"][0]["title"])
        self.assertIn("商业信誉", section["children"][1]["title"])
        # stat_02 未确认 → 应有（待准备）标记
        self.assertIn("待准备", section["children"][1]["title"])

    def test_empty_label_no_analysis_data(self):
        """无 analysis_data 时，空 label 回退到 check_key 显示。"""
        classified = {
            "qualification": [
                FakeCheckItem("qual_stat_01", confirmed_flag=False, check_label="", check_value=""),
            ]
        }
        ctx = {}
        section = _build_qualification_section(classified, ctx, None)
        self.assertEqual(len(section["children"]), 1)
        # label 为空且无 analysis_data、未确认 → 显示 "（一）（待准备）"
        self.assertIn("待准备", section["children"][0]["title"])
        # description 回退到 "资格证明材料"
        self.assertEqual(section["children"][0]["description"], "资格证明材料")

    def test_label_already_set_no_fallback_needed(self):
        """check_label 已有值时不走回退逻辑。"""
        classified = {
            "qualification": [
                FakeCheckItem("qual_stat_01", confirmed_flag=True, check_label="营业执照", check_value="需提供复印件"),
            ]
        }
        filtered_data = {
            "eligibility": {
                "qualifications": [
                    {"id": "stat_01", "requirement": "具有独立承担民事责任的能力"},
                ],
            }
        }
        ctx = {}
        section = _build_qualification_section(classified, ctx, filtered_data)
        # 即使 analysis_data 有数据，也应使用已有的 check_label
        self.assertIn("营业执照", section["children"][0]["title"])
        self.assertNotIn("独立承担", section["children"][0]["title"])


class TestBuildScoringSectionEnhanced(unittest.TestCase):
    """测试评分标准章节增强功能。"""

    def test_skip_heji_dimension(self):
        """跳过包含"合计"的汇总维度。"""
        data = {
            "scoring": {
                "dimensions": [
                    {"name": "报价", "score": 30},
                    {"name": "服务方案", "score": 40},
                    {"name": "合计（100分）", "score": 100},
                ],
            }
        }
        section = _build_scoring_section(data)
        # 应只有2个子章节，跳过了"合计"
        self.assertEqual(len(section["children"]), 2)
        self.assertNotIn("合计", section["children"][0]["title"])

    def test_criteria_json_string_parsed(self):
        """criteria 为 JSON 数组字符串时解析为可读文本。"""
        data = {
            "scoring": {
                "dimensions": [
                    {
                        "name": "评审因素",
                        "score": 0,
                        "criteria": '[{"name": "报价", "score": 30}, {"name": "技术", "score": 50}]',
                    },
                ],
            }
        }
        section = _build_scoring_section(data)
        self.assertEqual(len(section["children"]), 1)
        # 描述应为解析后的可读文本
        self.assertIn("报价", section["children"][0]["description"])

    def test_dimensions_only_names(self):
        """维度只有 name 字段时也能正常处理。"""
        data = {
            "scoring": {
                "dimensions": ["报价", "业绩"],
            }
        }
        section = _build_scoring_section(data)
        self.assertEqual(len(section["children"]), 2)
        self.assertIn("报价", section["children"][0]["title"])
        self.assertIn("业绩", section["children"][1]["title"])


class TestBuildPackageAwareOutlineScoringFallback(unittest.TestCase):
    """测试评分 fallback 路径中的 JSON 解析。"""

    @patch("app.service_modules.task_pipeline.catalog._extract_analysis_context")
    def test_scoring_from_ctx_json_array(self, mock_extract):
        """scoring_items 为 JSON 数组字符串时能正确解析为维度。"""
        mock_extract.return_value = {
            "overview": "", "technical_requirements": "", "business_requirements": "",
            "scoring_items": '[{"name": "报价", "score": 30}, {"name": "技术", "score": 50}]',
        }

        from app.service_modules.task_pipeline.catalog import _build_package_aware_outline

        task = MagicMock()
        analysis_result = MagicMock()
        filtered_data = {"packages": [], "has_package": False, "scoring": {}}
        classified = {"qualification": [], "compliance": [], "disqualification": [], "scoring": []}

        outline = _build_package_aware_outline(task, analysis_result, filtered_data, classified)
        # 应包含评分响应章节
        titles = [s["title"] for s in outline]
        scoring_titles = [t for t in titles if "评分" in t]
        self.assertGreater(len(scoring_titles), 0)


class TestBuildQualificationSectionPlaceholder(unittest.TestCase):
    """测试 check_label 为"核对项"占位符时也能回退。"""

    def test_placeholder_label_with_analysis_data(self):
        """check_label="核对项"时触发回退到 analysis_data。"""
        classified = {
            "qualification": [
                FakeCheckItem("qual_stat_01", confirmed_flag=True, check_label="核对项", check_value=""),
            ]
        }
        filtered_data = {
            "eligibility": {
                "qualifications": [
                    {"id": "stat_01", "requirement": "具有独立承担民事责任的能力（营业执照）"},
                ],
            }
        }
        section = _build_qualification_section(classified, {}, filtered_data)
        self.assertEqual(len(section["children"]), 1)
        self.assertIn("营业执照", section["children"][0]["title"])

    def test_placeholder_label_no_analysis_data(self):
        """check_label="核对项"但无 analysis_data → 保持显示。"""
        classified = {
            "qualification": [
                FakeCheckItem("qual_stat_01", confirmed_flag=True, check_label="核对项", check_value=""),
            ]
        }
        section = _build_qualification_section(classified, {}, None)
        self.assertEqual(len(section["children"]), 1)
        # 无 fallback，仍显示"核对项"
        self.assertIn("核对项", section["children"][0]["title"])


class TestBuildPackageAwareOutlineNumbering(unittest.TestCase):
    """测试动态编号：跳过可选章节后编号仍然连续。"""

    @patch("app.service_modules.task_pipeline.catalog._extract_analysis_context")
    def test_sequential_numbering_when_optional_skipped(self, mock_extract):
        """跳过资格证明和实质性要求后，技术参数应从四开始而非六。"""
        mock_extract.return_value = {
            "overview": "测试", "technical_requirements": "", "business_requirements": "",
            "scoring_items": "",
        }

        from app.service_modules.task_pipeline.catalog import _build_package_aware_outline

        task = MagicMock()
        analysis_result = MagicMock()
        filtered_data = {"packages": [], "has_package": False, "scoring": {}}
        # 无 qualification + 无 compliance → 跳过第4/5章
        classified = {"qualification": [], "compliance": [], "disqualification": [], "scoring": []}

        outline = _build_package_aware_outline(task, analysis_result, filtered_data, classified)
        titles = [s["title"] for s in outline]
        
        # 验证编号连续
        # 一、投标函
        self.assertTrue(titles[0].startswith("一、"), f"Expected 一、 got {titles[0]}")
        # 二、报价部分
        self.assertTrue(titles[1].startswith("二、"), f"Expected 二、 got {titles[1]}")
        # 三、法定代表人授权书
        self.assertTrue(titles[2].startswith("三、"), f"Expected 三、 got {titles[2]}")
        # 四、技术参数响应（跳过资格+实质性要求后，技术参数变成四）
        self.assertTrue(titles[3].startswith("四、"), f"Expected 四、 got {titles[3]}")
        # 五、商务要求响应
        self.assertTrue(titles[4].startswith("五、"), f"Expected 五、 got {titles[4]}")

    @patch("app.service_modules.task_pipeline.catalog._extract_analysis_context")
    def test_sequential_numbering_all_sections(self, mock_extract):
        """所有可选章节都出现时，编号应连续从一到十一。"""
        mock_extract.return_value = {
            "overview": "测试",
            "technical_requirements": "PCR试剂技术参数要求共27项",
            "business_requirements": "合同签订后30天内交货",
            "scoring_items": '[{"name": "报价", "score": 30}]',
        }

        from app.service_modules.task_pipeline.catalog import _build_package_aware_outline

        task = MagicMock()
        analysis_result = MagicMock()
        filtered_data = {
            "packages": [{"parameters": {"starred_count": 10}}],
            "has_package": False,
            "scoring": {"dimensions": [{"name": "报价", "score": 30}]},
        }
        classified = {
            "qualification": [FakeCheckItem("qual_01", check_label="营业执照")],
            "compliance": [FakeCheckItem("star_01", check_label="不接受联合体")],
            "disqualification": [],
            "scoring": [],
        }

        outline = _build_package_aware_outline(task, analysis_result, filtered_data, classified)
        titles = [s["title"] for s in outline]
        
        # 所有章节编号应连续无跳跃
        expected_prefixes = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一"]
        self.assertEqual(len(titles), len(expected_prefixes))
        for i, prefix in enumerate(expected_prefixes):
            self.assertTrue(
                titles[i].startswith(f"{prefix}、"),
                f"Section {i}: expected {prefix}、 got {titles[i]}",
            )
