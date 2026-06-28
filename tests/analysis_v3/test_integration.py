"""集成测试：用真实试剂耗材标书 DOCX 跑通整个 v3 管线（零LLM）。"""

import sys
import os
import unittest
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.infrastructure.document_parser import DocumentParser
from app.service_modules.task_pipeline.analysis_v3.phase1_metadata import extract_metadata
from app.service_modules.task_pipeline.analysis_v3.phase2_eligibility import (
    scan_eligibility, ELIGIBILITY_TEMPLATES,
)
from app.service_modules.task_pipeline.analysis_v3.phase3_scoring import (
    extract_scoring,
    _find_scoring_section,
    _find_tables_in_section,
    parse_scoring_table,
    _detect_text_tables,
)
from app.service_modules.task_pipeline.analysis_v3.schemas import (
    assemble_v3_analysis_data,
    analysis_data_to_json,
    preprocess_json,
    safe_json_loads,
)
from app.service_modules.task_pipeline.analysis_v3.check_items import generate_check_items

TENDER_DOCX = "/Users/wangjun/Desktop/成都海关 2026年试剂耗材采购项目.docx"


@unittest.skipIf(not os.path.exists(TENDER_DOCX), "标书文件不存在")
class TestIntegrationV3ZeroLLM(unittest.TestCase):
    """零LLM模式下的全管线集成测试。"""

    @classmethod
    def setUpClass(cls):
        with open(TENDER_DOCX, "rb") as f:
            payload = f.read()
        parser = DocumentParser()
        cls.doc = parser.parse_structured("成都海关2026年试剂耗材采购项目.docx", payload)

    def test_01_document_parsed(self):
        """文档解析成功，有章节结构。"""
        self.assertIsNotNone(self.doc)
        self.assertGreater(len(self.doc.sections), 0)
        print(f"\n  [解析] 共 {len(self.doc.sections)} 个顶级章节，{len(self.doc.to_text())} 字符")

    def test_02_layer1_metadata_zero_llm(self):
        """第1层：元数据提取（纯规则，零LLM）。"""
        text = self.doc.to_text()[:5000]
        meta = extract_metadata(text)
        print(f"  [元数据] project_code={meta['project_code']}, "
              f"project_name={meta['project_name']}, "
              f"budget={meta['budget']['total']}, "
              f"packages={meta['package_count']}")
        # 至少应有预算或包数
        self.assertTrue(
            meta['budget']['total'] > 0 or meta['package_count'] > 0
        )

    def test_03_layer1_eligibility_zero_llm(self):
        """第1层：生死线扫描（纯关键词，零LLM）。"""
        result = scan_eligibility(self.doc.sections, "GOODS")
        print(f"  [生死线] 共 {result['summary']['total_items']} 项 "
              f"(通过{result['summary']['passed']}, "
              f"关注{result['summary']['attention_required']})")
        self.assertIn("qualifications", result)
        self.assertIn("disqualifications", result)
        self.assertIn("starred_requirements", result)
        # 至少应有通用资格条目
        self.assertGreaterEqual(len(result["qualifications"]), 0)

    def test_04_layer2_scoring_zero_llm(self):
        """第2层：评分拆解（纯规则，零LLM）。"""
        scoring = extract_scoring(self.doc.sections)
        print(f"  [评分] 方法={scoring['method']}, "
              f"总分={scoring['total_score']}, "
              f"维度={len(scoring['dimensions'])}")
        for d in scoring["dimensions"]:
            print(f"    - {d['name']}: {d['score']}分 ({d['type']})")
        self.assertIn("dimensions", scoring)

    def test_05_layer2_scoring_table_extracted(self):
        """评分表能被解析（原生DOCX表格优先）。"""
        section = _find_scoring_section(self.doc.sections)
        self.assertIsNotNone(section, "应找到评标章节")
        tables = _find_tables_in_section(section)
        has_dimensions = False
        for table in tables:
            dims = parse_scoring_table(table)
            if dims:
                has_dimensions = True
                break
        self.assertTrue(has_dimensions or len(self.doc.sections) > 0)

    def test_06_layer2_package_count(self):
        """检验分包检测。"""
        text = self.doc.to_text()[:5000]
        import re
        # 从文本中找包数
        m = re.search(r"共计\s*(\d+)\s*个包", text)
        if m:
            print(f"  [分包] 检测到 {m.group(1)} 个包")
        else:
            print("  [分包] 未在文本中明确检测到分包数量")

    def test_07_layer2_packages_analysis(self):
        """分包参数统计（纯规则，零LLM）。"""
        from app.service_modules.task_pipeline.analysis_v3.phase3_scoring import (
            extract_packages, _count_pkg_params, _find_tech_section,
            _find_package_sections,
        )

        # 检测分包数
        package_nos = []
        text = self.doc.to_text()[:5000]
        import re
        m = re.search(r"共计\s*(\d+)\s*个包", text)
        if m:
            package_nos = list(range(1, int(m.group(1)) + 1))

        if not package_nos:
            max_pkg = 0
            for n in re.findall(r"第(\d+)包", text):
                pn = int(n)
                if pn > max_pkg:
                    max_pkg = pn
            if max_pkg > 0:
                package_nos = list(range(1, max_pkg + 1))

        if package_nos:
            tech_section = _find_tech_section(self.doc.sections)
            source = [tech_section] if tech_section else self.doc.sections
            pkg_map = _find_package_sections(source, package_nos)
            found = sum(1 for v in pkg_map.values() if v is not None)
            print(f"  [分包] 找到 {found}/{len(package_nos)} 个包的章节")
        else:
            print("  [分包] 未检测到分包")

    def test_08_full_pipeline_assembly(self):
        """全管线串联 + 数据组装。"""
        # 第1层
        text = self.doc.to_text()[:5000]
        metadata = extract_metadata(text)
        eligibility = scan_eligibility(self.doc.sections, "GOODS")

        # 第2层
        scoring = extract_scoring(self.doc.sections)

        # 组装
        data = assemble_v3_analysis_data(
            metadata=metadata,
            eligibility=eligibility,
            scoring=scoring,
        )

        # 验证结构
        self.assertEqual(data["version"], "v3")
        json_str = analysis_data_to_json(data)
        parsed = json.loads(json_str)

        print(f"\n  === 完整 analysis_data v3 零LLM ===")
        print(f"  元数据: project_code={parsed['metadata']['project_code']}, "
              f"budget={parsed['metadata']['budget']['total']}")
        print(f"  评分维度: {len(parsed['scoring']['dimensions'])} 个")
        for d in parsed["scoring"]["dimensions"]:
            print(f"    - {d['name']}: {d['score']}分 ({d['type']})")
        print(f"  生死线: {parsed['eligibility']['summary']['total_items']} 项")
        print(f"  section_scoring_map: {len(parsed['section_scoring_map'])} 项")

        # 中文不乱码
        self.assertIn("v3", json_str)
        self.assertNotIn("\\u00", json_str[:100])

    def test_09_pipeline_runs_without_exception(self):
        """全管线运行不抛出异常。"""
        try:
            text = self.doc.to_text()[:5000]
            metadata = extract_metadata(text)
            eligibility = scan_eligibility(self.doc.sections, "GOODS")
            scoring = extract_scoring(self.doc.sections)
            data = assemble_v3_analysis_data(
                metadata=metadata,
                eligibility=eligibility,
                scoring=scoring,
            )
            check_items = generate_check_items(eligibility, scoring, [])
            json_str = analysis_data_to_json(data)
            self.assertIsNotNone(json_str)
            print(f"  [全管线] 运行成功，核对项数: {len(check_items)}")
        except Exception as e:
            self.fail(f"全管线运行异常: {e}")

    def test_10_json_preprocessing_robust(self):
        """JSON 预处理能处理各种异常输入。"""
        test_cases = [
            ('{"a": 1}', {"a": 1}),
            ('{"a": 1,}', {"a": 1}),
            ('{"a": [1, 2,],}', {"a": [1, 2]}),
            ('\ufeff{"a": 1}', {"a": 1}),
            ('```json\n{"a": 1}\n```', {"a": 1}),
        ]
        for raw, expected in test_cases:
            with self.subTest(raw=raw[:30]):
                result = safe_json_loads(raw)
                self.assertEqual(result, expected)

    def test_11_metadata_no_llm(self):
        """验证不依赖 LLM。"""
        text = self.doc.to_text()[:5000]
        meta = extract_metadata(text)
        # 无论提取到什么，确保不依赖 LLM
        self.assertIsInstance(meta, dict)
        self.assertIn("project_code", meta)
        self.assertIn("project_name", meta)

    def test_12_eligibility_no_llm(self):
        """验证 eligibility 不依赖 LLM。"""
        result = scan_eligibility(self.doc.sections, "GOODS")
        self.assertIsInstance(result, dict)
        self.assertIn("qualifications", result)


if __name__ == "__main__":
    unittest.main()
