"""技术要求模块：从多源合并组装，并将长文本拆为可召回的原子条目。

数据源优先级：
  1. analysis_data._comprehensive.technical_requirements（结构化段级提取）
  2. analysis_data.table_classification.tech_requirements（技术规格表）
  3. analysis_data.table_classification.product_lists（产品清单/规格）
  4. analysis_data.metadata.extra.technical_items（规则/LLM 补充）
  5. analysis_data.format_requirements.required_sections（历史模板表格兜底）
  6. result.technical_requirements（DB 扁平列兜底，占位文本被过滤）
"""
import logging
import json
import re

logger = logging.getLogger(__name__)


# 已知占位文本列表（分析管线未提取到技术要求时写入的默认值）
_PLACEHOLDER_PATTERNS = [
    "暂未提取到技术要求",
    "暂未提取到",
    "未提取到技术要求",
    "暂无技术要求",
    "无技术要求",
]

_ENUM_START_RE = re.compile(
    r"(?m)(?=^\s*(?:\d+(?:\.\d+){1,4}[、.．:]?|[（(]?\d+[)）]|[一二三四五六七八九十]+、|[①②③④⑤⑥⑦⑧⑨⑩]|★|▲))"
)
_INLINE_ENUM_RE = re.compile(r"(?<!\d)(?=(?:\d+(?:\.\d+){1,4}\s))")
_INLINE_ENUM_AFTER_PUNCT_RE = re.compile(r"(?<=[。；;])\s*(?=\d+(?:\.\d+){1,4}\s*)")


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


def _normalize_text(text: str) -> str:
    """规范化文本，保留必要换行以便后续拆分。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _split_clause_text(text: str) -> list:
    """按分号/句号拆分长句，避免返回整段大文本。"""
    clauses = [part.strip() for part in re.split(r"[；;]\s*", text) if part.strip()]
    if len(clauses) <= 1 and len(text) > 180:
        clauses = [part.strip() for part in re.split(r"(?<=。)", text) if part.strip()]
    if len(clauses) <= 1:
        return [text.strip()]

    merged = []
    buffer = ""
    for clause in clauses:
        if not buffer:
            buffer = clause
            continue
        if len(buffer) < 50:
            buffer = f"{buffer} {clause}".strip()
            continue
        merged.append(buffer)
        buffer = clause
    if buffer:
        merged.append(buffer)
    return merged


def split_technical_text(text: str) -> list:
    """将技术长文本拆分为适合召回的原子条目。"""
    normalized = _normalize_text(text)
    if not normalized:
        return []

    expanded = normalized
    if "\n" not in expanded:
        expanded = _INLINE_ENUM_AFTER_PUNCT_RE.sub("\n", expanded)
        expanded = _INLINE_ENUM_RE.sub("\n", expanded)

    enum_parts = [part.strip() for part in _ENUM_START_RE.split(expanded) if part.strip()]
    if len(enum_parts) > 1:
        chunks = enum_parts
    else:
        lines = [line.strip() for line in expanded.split("\n") if line.strip()]
        chunks = lines if len(lines) > 1 else [expanded]

    results = []
    seen_local = set()
    for chunk in chunks:
        sub_chunks = _split_clause_text(chunk) if len(chunk) > 180 else [chunk]
        for item in sub_chunks:
            cleaned = item.strip(" \n\t;；")
            if len(cleaned) < 4 or cleaned in seen_local or _is_placeholder(cleaned):
                continue
            seen_local.add(cleaned)
            results.append(cleaned)
    return results or ([normalized] if normalized else [])


def _append_atomic_items(items: list, seen: set, text: str, source_section: str) -> None:
    """将文本拆分为原子条目后追加到结果集中。"""
    for piece in split_technical_text(text):
        if piece and piece not in seen:
            seen.add(piece)
            items.append({"content": piece, "source_section": source_section})


def _collect_from_comprehensive(analysis: dict, seen: set) -> list:
    """从 _comprehensive 结构化列表提取技术要求。"""
    items = []
    for tr in analysis.get("_comprehensive", {}).get("technical_requirements", []):
        text = tr.get("requirement", "").strip()
        _append_atomic_items(items, seen, text, "comprehensive")
    return items


def _collect_from_table_classification(analysis: dict, seen: set) -> list:
    """从 table_classification 中提取技术规格和产品规格。"""
    items = []
    tc = analysis.get("table_classification", {})
    if not isinstance(tc, dict):
        return items
    for tbl in tc.get("tech_requirements", []) or []:
        if not isinstance(tbl, dict):
            continue
        for row in tbl.get("items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            spec = str(row.get("specification", "")).strip()
            text = f"{name}: {spec}" if name and spec else name
            _append_atomic_items(items, seen, text, "table_classification.tech")
    for tbl in tc.get("product_lists", []) or []:
        if not isinstance(tbl, dict):
            continue
        for row in tbl.get("items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            spec = str(row.get("specification", "")).strip()
            remark = str(row.get("remark", "")).strip()
            text = f"{name}: {spec}" if name and spec else (f"{name}: {remark}" if name and remark else name)
            _append_atomic_items(items, seen, text, "table_classification.product")
    return items


def _collect_from_metadata_extra(analysis: dict, seen: set) -> list:
    """从 metadata.extra.technical_items 提取规则/LLM 补充的技术项。"""
    items = []
    meta = analysis.get("metadata", {}) or {}
    extra = meta.get("extra", {}) or {}
    if not isinstance(extra, dict):
        return items
    for entry in extra.get("technical_items", []) or []:
        if isinstance(entry, dict):
            text = entry.get("requirement") or entry.get("text") or ""
        else:
            text = entry
        _append_atomic_items(items, seen, text, "metadata.extra")
    if not items:
        _append_atomic_items(items, seen, extra.get("technical_terms_raw", ""), "metadata.extra")
    return items


def _collect_from_format_sections(analysis: dict, seen: set) -> list:
    """从 format_requirements 中兼容提取历史表格技术项。"""
    items = []
    fmt = analysis.get("format_requirements", {})
    if not fmt:
        return items
    req_secs = fmt.get("required_sections", [])
    for sec in _find_sections_by_type(req_secs, "technical"):
        for blk in sec.get("template_content", []):
            if blk.get("type") != "table":
                continue
            headers = blk.get("headers", [])
            rows = blk.get("rows", [])
            if not headers or not rows:
                continue
            for row_dict in _table_to_dicts(headers, rows):
                name = row_dict.get("技术要求名称", "") or row_dict.get(headers[0], "")
                params = row_dict.get("技术参数与性能指标", "") or (row_dict.get(headers[1], "") if len(headers) > 1 else "")
                name = name.strip()
                params = params.strip()
                text = f"{name}: {params}" if name and params else name
                _append_atomic_items(items, seen, text, "format_tech")
    for sec in _find_sections_by_type(req_secs, "price_list"):
        for blk in sec.get("template_content", []):
            if blk.get("type") != "table":
                continue
            headers = blk.get("headers", [])
            rows = blk.get("rows", [])
            if not headers or not rows:
                continue
            for row_dict in _table_to_dicts(headers, rows):
                name = row_dict.get("采购产品名称", "") or row_dict.get("产品名称", "") or ""
                name = name.strip()
                if not name:
                    continue
                spec = (
                    row_dict.get("★规格参数", "")
                    or row_dict.get("技术参数与性能指标", "")
                    or row_dict.get("规格参数", "")
                    or row_dict.get("规格", "")
                    or ""
                ).strip()
                _append_atomic_items(items, seen, f"{name}: {spec}" if spec else name, "format_product")
    return items


def _collect_from_db_column(result, seen: set) -> list:
    """从 DB 顶层 technical_requirements 兜底提取。"""
    items = []
    tech_payload = result.technical_requirements
    if not tech_payload:
        return items
    parsed = None
    if isinstance(tech_payload, str):
        try:
            parsed = json.loads(tech_payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = tech_payload
    else:
        parsed = tech_payload

    if isinstance(parsed, dict):
        for entry in parsed.get("items", []) or []:
            if isinstance(entry, dict):
                text = entry.get("content", "") or entry.get("requirement", "")
            else:
                text = entry
            _append_atomic_items(items, seen, text, "db_fallback")
        return items

    tech_text = str(parsed or "").strip()
    if not tech_text or _is_placeholder(tech_text):
        return items
    _append_atomic_items(items, seen, tech_text, "db_fallback")
    return items


def build_technical_section(result, analysis: dict) -> dict:
    """组装结构化技术要求，供分析结果与 check-items 复用。"""
    seen = set()
    all_items = []

    n1 = len(all_items)
    all_items.extend(_collect_from_comprehensive(analysis, seen))
    n1 = len(all_items) - n1

    n2 = len(all_items)
    all_items.extend(_collect_from_table_classification(analysis, seen))
    n2 = len(all_items) - n2

    n3 = len(all_items)
    all_items.extend(_collect_from_metadata_extra(analysis, seen))
    n3 = len(all_items) - n3

    n4 = len(all_items)
    all_items.extend(_collect_from_format_sections(analysis, seen))
    n4 = len(all_items) - n4

    n5 = len(all_items)
    all_items.extend(_collect_from_db_column(result, seen))
    n5 = len(all_items) - n5

    logger.info(
        "technical: comprehensive=%d, table_classification=%d, metadata_extra=%d, format_sections=%d, db_fallback=%d, total=%d",
        n1, n2, n3, n4, n5, len(all_items),
    )

    if n1 == 0:
        comp = analysis.get("_comprehensive", {})
        if isinstance(comp, dict):
            tr = comp.get("technical_requirements", [])
            if not tr:
                logger.warning("technical: _comprehensive.technical_requirements 为空，请检查 segmentation 和 table_classification 管线")

    return {"items": all_items}


def assemble_technical(result, analysis: dict) -> dict:
    """兼容 check-items 的技术要求组装入口。"""
    return build_technical_section(result, analysis)
