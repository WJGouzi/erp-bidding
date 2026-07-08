"""技术要求模块：从多源合并组装。

数据源优先级：
  1. analysis_data._comprehensive.technical_requirements（结构化段级提取）
  2. analysis_data.format_requirements.required_sections.tech_requirements（技术要求表格）
  3. analysis_data.format_requirements.required_sections.product_lists（产品清单含规格参数）
  4. result.technical_requirements（DB 扁平列兜底，占位文本被过滤）
"""
import logging

logger = logging.getLogger(__name__)


# 已知占位文本列表（分析管线未提取到技术要求时写入的默认值）
_PLACEHOLDER_PATTERNS = [
    "暂未提取到技术要求",
    "暂未提取到",
    "未提取到技术要求",
    "暂无技术要求",
    "无技术要求",
]



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


def _is_placeholder(text: str) -> bool:
    """判断是否为占位文本。"""
    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern in text:
            return True
    return False


def _collect_from_comprehensive(analysis: dict, seen: set) -> list:
    """从 _comprehensive 结构化列表提取技术要求。"""
    items = []
    for tr in analysis.get("_comprehensive", {}).get("technical_requirements", []):
        text = tr.get("requirement", "").strip()
        if text and text not in seen:
            seen.add(text)
            items.append({"content": text, "source_section": "comprehensive"})
    return items


def _collect_from_tech_tables(analysis: dict, seen: set) -> list:
    """从 required_sections 中技术要求章节的表格提取。"""
    items = []
    fmt = analysis.get("format_requirements", {})
    if not fmt:
        return items
    req_secs = fmt.get("required_sections", [])
    for sec in _find_sections_by_type(req_secs, "technical"):
        for blk in sec.get("template_content", []):
            if blk.get("type") != "table":
                continue
            tbl = blk
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            if not headers or not rows:
                continue
            for row_dict in _table_to_dicts(headers, rows):
                name = row_dict.get("技术要求名称", "") or row_dict.get(headers[0], "")
                params = row_dict.get("技术参数与性能指标", "") or (row_dict.get(headers[1], "") if len(headers) > 1 else "")
                name = name.strip()
                params = params.strip()
                if name and params:
                    text = f"{name}: {params}"
                    if text not in seen:
                        seen.add(text)
                        items.append({"content": text, "source_section": "table_tech"})
    return items


def _collect_from_product_lists(analysis: dict, seen: set) -> list:
    """从 required_sections 中产品/报价章节的表格提取规格参数。"""
    items = []
    fmt = analysis.get("format_requirements", {})
    if not fmt:
        return items
    req_secs = fmt.get("required_sections", [])
    for sec in _find_sections_by_type(req_secs, "price_list"):
        for blk in sec.get("template_content", []):
            if blk.get("type") != "table":
                continue
            tbl = blk
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            if not headers or not rows:
                continue
            for row_dict in _table_to_dicts(headers, rows):
                name = row_dict.get("采购产品名称", "") or row_dict.get("产品名称", "") or ""
                name = name.strip()
                if not name:
                    continue
                spec = (row_dict.get("★规格参数", "") or row_dict.get("技术参数与性能指标", "")
                        or row_dict.get("规格参数", "") or row_dict.get("规格", "") or "")
                spec = spec.strip()
                if spec:
                    text = f"{name}: {spec}"
                else:
                    text = name
                if text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": "product_list"})
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
    """从 DB 扁平列兜底提取（过滤占位文本）。"""
    items = []
    tech_text = result.technical_requirements or ""
    if not tech_text.strip() or _is_placeholder(tech_text):
        return items

    for line in _parse_flat_text(tech_text):
        if line and line not in seen:
            seen.add(line)
            items.append({"content": line, "source_section": "db_fallback"})
    return items


def assemble_technical(result, analysis: dict) -> dict:
    """组装技术要求：多源合并，去重，优先级回退。"""
    seen = set()
    all_items = []

    # 源 1: _comprehensive 结构化
    all_items.extend(_collect_from_comprehensive(analysis, seen))
    # 源 2: 表格-技术要求
    all_items.extend(_collect_from_tech_tables(analysis, seen))
    # 源 3: 产品清单
    all_items.extend(_collect_from_product_lists(analysis, seen))
    # 源 4: DB 扁平列兜底
    all_items.extend(_collect_from_db_column(result, seen))

    return {
        "items": all_items,
        "raw": "",
    }
