"""商务要求模块：从多源合并组装。

数据源优先级：
  1. analysis_data._comprehensive.business_requirements（结构化段级提取）
  2. analysis_data.format_requirements.required_sections.business_requirements（商务要求表格）
  3. analysis_data.format_requirements.required_sections.service_requirements（服务要求表格）
  4. analysis_data.metadata.extra（元数据扩展字段）
  5. result.business_requirements（DB 扁平列兜底）
"""
import logging

from app.domain.analysis_schema import EXTRA_LABELS

logger = logging.getLogger(__name__)
def _find_sections_by_type(required_sections, file_type):
    """从 required_sections 中按 file_type 查找匹配章节。"""
    if not required_sections:
        return []
    return [s for s in required_sections if s.get("file_type") == file_type]


def _table_to_dicts(headers, rows):
    """将表格 headers+rows 转为 [{header: cell, ...}, ...] 列表。"""
    result = []
    for row in rows:
        item = {}
        for i, h in enumerate(headers):
            if i < len(row):
                item[h] = row[i]
        result.append(item)
    return result





# EXTRA_LABELS 未覆盖但实际存在的 extra 字段（来自 extraction 输出）
_EXTRA_ONLY_FIELDS = {
    "submission_copy_detail": "递交副本详情",
    "pkg_special_qual": "包特殊资格",
}


def _collect_from_comprehensive(analysis: dict, seen: set) -> list:
    """从 _comprehensive 结构化列表提取商务要求。"""
    items = []
    for br in analysis.get("_comprehensive", {}).get("business_requirements", []):
        text = br.get("requirement", "").strip()
        if text and text not in seen:
            seen.add(text)
            items.append({"content": text, "source_section": "comprehensive"})
    return items


def _collect_from_business_tables(analysis: dict, seen: set) -> list:
    """从 required_sections 中商务章节的表格提取。"""
    items = []
    fmt = analysis.get("format_requirements", {})
    if not fmt:
        return items
    req_secs = fmt.get("required_sections", [])
    for sec in _find_sections_by_type(req_secs, "business"):
        for tbl in sec.get("template_tables", []):
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            if not headers or not rows:
                continue
            for row_dict in _table_to_dicts(headers, rows):
                text = row_dict.get("商务要求内容", "") or row_dict.get("商务要求名称", "") or ""
                text = text.strip()
                if text and text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": "table_business"})
    return items


def _collect_from_service_tables(analysis: dict, seen: set) -> list:
    """从 required_sections 中服务章节的表格提取。"""
    items = []
    fmt = analysis.get("format_requirements", {})
    if not fmt:
        return items
    req_secs = fmt.get("required_sections", [])
    for sec in _find_sections_by_type(req_secs, "service"):
        for tbl in sec.get("template_tables", []):
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            if not headers or not rows:
                continue
            for row_dict in _table_to_dicts(headers, rows):
                text = row_dict.get("服务要求内容", "") or row_dict.get("服务要求名称", "") or ""
                text = text.strip()
                if text and text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": "table_service"})
    return items


def _collect_from_extra(analysis: dict, seen: set) -> list:
    """从 metadata.extra 提取商务字段。"""
    items = []
    extra = analysis.get("metadata", {}).get("extra", {})
    if not isinstance(extra, dict):
        return items

    # 按 EXTRA_LABELS 顺序输出，保证可预测性
    for field_key, field_label in EXTRA_LABELS:
        val = extra.get(field_key)
        if val and str(val).strip():
            text = f"{field_label}：{val}"
            if text not in seen:
                seen.add(text)
                items.append({"content": text, "source_section": f"extra.{field_key}"})

    # 补充 EXTRA_LABELS 未覆盖的 extra 字段
    for field_key, field_label in _EXTRA_ONLY_FIELDS.items():
        val = extra.get(field_key)
        if val and str(val).strip():
            text = f"{field_label}：{val}"
            if text not in seen:
                seen.add(text)
                items.append({"content": text, "source_section": f"extra.{field_key}"})

    return items


def _parse_flat_text(text: str) -> list:
    """将扁平文本按行拆分为条目列表。"""
    if not text or not text.strip():
        return []
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return [text.strip()]
    return lines


def _collect_from_db_column(result, seen: set) -> list:
    """从 DB 扁平列兜底提取（仅在其他源都为空时触发）。"""
    items = []
    biz_text = result.business_requirements or ""
    if not biz_text.strip():
        return items

    for line in _parse_flat_text(biz_text):
        if line and line not in seen:
            seen.add(line)
            items.append({"content": line, "source_section": "db_fallback"})
    return items


def assemble_business(result, analysis: dict) -> dict:
    """组装商务要求：多源合并，去重，优先级回退。"""
    seen = set()
    all_items = []

    # 源 1: _comprehensive 结构化
    all_items.extend(_collect_from_comprehensive(analysis, seen))
    # 源 2: 表格-商务要求
    all_items.extend(_collect_from_business_tables(analysis, seen))
    # 源 3: 表格-服务要求
    all_items.extend(_collect_from_service_tables(analysis, seen))
    # 源 4: metadata.extra
    all_items.extend(_collect_from_extra(analysis, seen))
    # 源 5: DB 扁平列兜底（仅在前置源都为空时才有实质产出）
    all_items.extend(_collect_from_db_column(result, seen))

    return {
        "items": all_items,
        "raw": "",
    }
