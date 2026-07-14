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
_NUMERIC_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})[、.．:]?\s*(.*)$")
_CN_HEADING_RE = re.compile(r"^\s*([一二三四五六七八九十]+)、\s*(.*)$")
_CN_PAREN_HEADING_RE = re.compile(r"^\s*[（(]([一二三四五六七八九十]+)[)）]\s*(.*)$")
_DIGIT_PAREN_HEADING_RE = re.compile(r"^\s*([（(]?\d+[)）])\s*(.*)$")
_CIRCLE_HEADING_RE = re.compile(r"^\s*([①②③④⑤⑥⑦⑧⑨⑩])\s*(.*)$")
_STAR_HEADING_RE = re.compile(r"^\s*([★▲])\s*(.*)$")

_SOURCE_GROUP_TITLES = {
    "comprehensive": "技术要求正文",
    "table_classification.tech": "技术规格参数",
    "table_classification.product": "采购产品清单",
    "metadata.extra": "补充技术要求",
    "format_tech": "技术模板要求",
    "format_product": "产品模板清单",
    "db_fallback": "历史技术要求",
}

_PACKAGE_REF_RE = re.compile(r"第\s*([A-Za-z0-9一二三四五六七八九十百零]+)\s*包")

_CN_PACKAGE_NO_MAP = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _normalize_package_no(value: str) -> str:
    """将包号统一规范为阿拉伯数字字符串。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return str(int(text))
    if text == "十":
        return "10"
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_PACKAGE_NO_MAP.get(left, 1 if left == "" else 0)
        units = _CN_PACKAGE_NO_MAP.get(right, 0)
        total = tens * 10 + units
        return str(total) if total > 0 else text
    if text in _CN_PACKAGE_NO_MAP:
        return str(_CN_PACKAGE_NO_MAP[text])
    return text


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
    normalized = _normalize_text(text)
    if (
        normalized
        and "\n" not in normalized
        and (
            _NUMERIC_HEADING_RE.match(normalized)
            or _CN_HEADING_RE.match(normalized)
            or _CN_PAREN_HEADING_RE.match(normalized)
            or _DIGIT_PAREN_HEADING_RE.match(normalized)
            or _CIRCLE_HEADING_RE.match(normalized)
            or _STAR_HEADING_RE.match(normalized)
        )
    ):
        pieces = [normalized]
    else:
        pieces = split_technical_text(text)
    for piece in pieces:
        if piece and piece not in seen:
            seen.add(piece)
            items.append({"content": piece, "source_section": source_section})


def _source_group_title(source_section: str) -> str:
    """将内部来源映射为更稳定的业务分组标题。"""
    for key, title in _SOURCE_GROUP_TITLES.items():
        if source_section == key or source_section.startswith(f"{key}."):
            return title
    return "其他技术要求"


def _load_packages(result, analysis: dict) -> list:
    """从 analysis 或 DB 顶层读取包信息。"""
    packages = analysis.get("packages", []) if isinstance(analysis, dict) else []
    if isinstance(packages, list) and packages:
        return [pkg for pkg in packages if isinstance(pkg, dict)]
    packages_json = getattr(result, "packages_json", None)
    if not packages_json:
        return []
    try:
        parsed = json.loads(packages_json) if isinstance(packages_json, str) else packages_json
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return [pkg for pkg in parsed if isinstance(pkg, dict)] if isinstance(parsed, list) else []


def _detect_package_no_from_text(text: str) -> str:
    """从文本中识别包号。"""
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    match = _PACKAGE_REF_RE.search(normalized)
    return _normalize_package_no(match.group(1)) if match else ""


def _default_scope_key(packages: list) -> str:
    """根据包数量确定默认挂载范围。

    多包且无显式包引用时，以共享范围处理（技术章节锚点优先原则），
    确保 scope_mode 正确判定为 shared_only 或 mixed。
    """
    if not packages:
        return "scope:default"
    if len(packages) == 1:
        return f"package:{packages[0].get('package_no')}"
    return "scope:shared"


def _ensure_scope_node(scope_nodes: dict, scope_order: list, scope_key: str, packages: list) -> dict:
    """确保存在范围根节点。"""
    node = scope_nodes.get(scope_key)
    if node is not None:
        return node

    if scope_key.startswith("package:"):
        package_no = scope_key.split(":", 1)[1]
        pkg = next((p for p in packages if str(p.get("package_no", "")) == package_no), {}) if packages else {}
        title = str(pkg.get("name") or f"第{package_no}包").strip()
        node = {
            "id": f"tech-scope-{package_no}",
            "title": title,
            "content": "",
            "node_type": "package",
            "package_no": package_no,
            "source_section": "package",
            "level": 1,
            "parent_id": "",
            "children": [],
        }
    elif scope_key == "scope:shared":
        node = {
            "id": "tech-scope-shared",
            "title": "",
            "content": "",
            "node_type": "shared_scope",
            "package_no": "",
            "source_section": "shared",
            "level": 1,
            "parent_id": "",
            "children": [],
        }
    else:
        node = {
            "id": "tech-scope-default",
            "title": "",
            "content": "",
            "node_type": "scope",
            "package_no": "",
            "source_section": "default",
            "level": 1,
            "parent_id": "",
            "children": [],
        }
    scope_nodes[scope_key] = node
    scope_order.append(scope_key)
    return node


def _root_scope_for_text(text: str, packages: list) -> str:
    """根据文本中的包号标记或当前包配置，返回挂载范围。"""
    pkg_no = _detect_package_no_from_text(text)
    if pkg_no:
        return f"package:{pkg_no}"
    return _default_scope_key(packages)


def _make_node_title(_heading: str, body: str) -> str:
    """生成节点标题，仅保留去掉序号后的语义标题。"""
    body = str(body or "").strip()
    if not body:
        return ""
    return body if len(body) <= 40 else f"{body[:40]}..."


def _parse_heading_meta(text: str) -> dict:
    """识别条目的层级编号信息。"""
    raw = str(text or "").strip()
    if not raw:
        return {"raw": raw, "kind": "plain", "level": None, "codes": [], "heading": "", "body": ""}

    match = _NUMERIC_HEADING_RE.match(raw)
    if match:
        code = match.group(1).strip()
        body = match.group(2).strip()
        parts = code.split(".")
        codes = [".".join(parts[:i]) for i in range(1, len(parts) + 1)]
        return {"raw": raw, "kind": "numeric", "level": len(parts), "codes": codes, "heading": code, "body": body}

    for pattern, kind, level in (
        (_CN_HEADING_RE, "cn_top", 1),
        (_CN_PAREN_HEADING_RE, "cn_sub", 2),
        (_DIGIT_PAREN_HEADING_RE, "digit_sub", 2),
        (_CIRCLE_HEADING_RE, "circle", 3),
        (_STAR_HEADING_RE, "star", 3),
    ):
        match = pattern.match(raw)
        if match:
            heading = match.group(1).strip()
            body = match.group(2).strip()
            return {
                "raw": raw,
                "kind": kind,
                "level": level,
                "codes": [],
                "heading": heading,
                "body": body,
            }

    return {"raw": raw, "kind": "plain", "level": None, "codes": [], "heading": "", "body": raw}


def _create_tree_node(
    node_id: str,
    title: str,
    level: int,
    source_section: str,
    content: str = "",
    clause_no: str = "",
    node_type: str = "requirement",
    package_no: str = "",
) -> dict:
    """构造统一的技术要求树节点。"""
    return {
        "id": node_id,
        "title": title,
        "content": content,
        "clause_no": clause_no,
        "node_type": node_type,
        "package_no": package_no,
        "source_section": source_section,
        "level": level,
        "parent_id": "",
        "children": [],
    }


def _sanitize_tree_nodes(nodes: list) -> list:
    """移除内部临时字段，输出可持久化的树结构。"""
    result = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        children = _sanitize_tree_nodes(node.get("children", []) or [])
        item = {
            "id": node.get("id", ""),
            "content": node.get("content", ""),
            "node_type": node.get("node_type", "requirement"),
            "source_section": node.get("source_section", ""),
            "level": node.get("level", 0),
            "children": children,
        }
        title = str(node.get("title", "")).strip()
        package_no = str(node.get("package_no", "")).strip()
        clause_no = str(node.get("clause_no", "")).strip()
        parent_id = str(node.get("parent_id", "")).strip()
        if title and (children or not str(node.get("content", "")).strip()):
            item["title"] = title
        if package_no:
            item["package_no"] = package_no
        if clause_no:
            item["clause_no"] = clause_no
        if parent_id:
            item["parent_id"] = parent_id
        result.append(item)
    return result


def _copy_tree_nodes(nodes: list) -> list:
    """深拷贝技术要求树，避免过滤时修改原结构。"""
    copied = []
    if not isinstance(nodes, list):
        return copied
    for node in nodes:
        if not isinstance(node, dict):
            continue
        item = dict(node)
        item["children"] = _copy_tree_nodes(node.get("children", []) or [])
        copied.append(item)
    return copied


def _infer_scope_mode(items: list) -> str:
    """根据根节点类型推断技术要求归属模式。"""
    if not isinstance(items, list) or not items:
        return "unscoped"
    has_package = any(isinstance(node, dict) and node.get("node_type") == "package" for node in items)
    has_shared = any(isinstance(node, dict) and node.get("node_type") == "shared_scope" for node in items)
    if has_package and has_shared:
        return "mixed"
    if has_package:
        return "package_only"
    if has_shared:
        return "shared_only"
    return "unscoped"


def filter_technical_section(technical_section: dict | None, selected_package_no: str = "", has_package: bool = False) -> dict:
    """按已选包号过滤技术要求树。

    规则：
      - 无分包或未选包：返回原树
      - package_only：只保留当前包
      - shared_only：只保留共享节点
      - mixed：保留当前包和共享节点
      - unscoped：不过滤
    """
    section = technical_section if isinstance(technical_section, dict) else {}
    items = _copy_tree_nodes(section.get("items", []) or [])
    scope_mode = str(section.get("scope_mode", "")).strip() or _infer_scope_mode(items)

    if not has_package or not str(selected_package_no or "").strip():
        return {"scope_mode": scope_mode, "items": items}

    selected_package_no = str(selected_package_no).strip()
    if scope_mode == "shared_only":
        filtered = [
            node for node in items
            if isinstance(node, dict) and node.get("node_type") == "shared_scope"
        ]
    elif scope_mode == "package_only":
        filtered = [
            node for node in items
            if isinstance(node, dict)
            and node.get("node_type") == "package"
            and str(node.get("package_no", "")).strip() == selected_package_no
        ]
    elif scope_mode == "mixed":
        filtered = [
            node for node in items
            if isinstance(node, dict) and (
                node.get("node_type") == "shared_scope"
                or (
                    node.get("node_type") == "package"
                    and str(node.get("package_no", "")).strip() == selected_package_no
                )
            )
        ]
    else:
        filtered = items
    return {"scope_mode": scope_mode, "items": filtered}


def flatten_technical_nodes(technical_section: dict | list | None) -> list:
    """将技术要求树按深度优先展开为节点列表。"""
    nodes = technical_section
    if isinstance(technical_section, dict):
        nodes = technical_section.get("items", []) or []
    flat = []
    if not isinstance(nodes, list):
        return flat
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("content", "")).strip():
            flat.append(node)
        flat.extend(flatten_technical_nodes(node.get("children", []) or []))
    return flat


def render_technical_text(technical_section: dict | list | None) -> str:
    """将技术要求树渲染为层级文本。"""
    nodes = technical_section
    if isinstance(technical_section, dict):
        nodes = technical_section.get("items", []) or []

    def _render(nodes_: list, indent: int = 0) -> list[str]:
        lines = []
        if not isinstance(nodes_, list):
            return lines
        prefix = "  " * indent
        for node in nodes_:
            if not isinstance(node, dict):
                continue
            text = str(node.get("content", "")).strip() or str(node.get("title", "")).strip()
            if text:
                lines.append(f"{prefix}{text}")
            lines.extend(_render(node.get("children", []) or [], indent + 1))
        return lines

    return "\n".join(line for line in _render(nodes) if line.strip())


def _assign_tree_meta(nodes: list, parent: dict | None = None, level: int = 1) -> None:
    """递归回填层级与父节点等元信息。"""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node["parent_id"] = parent.get("id", "") if isinstance(parent, dict) else ""
        node["level"] = level
        _assign_tree_meta(node.get("children", []) or [], node, level + 1)


def _build_group_tree(source_section: str, items: list, node_seq: list[int], package_no: str = "") -> list:
    """按来源分组构建技术要求树，返回该分组下的根节点列表。"""
    roots = []
    numeric_nodes = {}
    level_stack = {}

    def _append_child(parent_node, child_node):
        if isinstance(parent_node, dict):
            parent_node.setdefault("children", []).append(child_node)
        else:
            roots.append(child_node)

    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        meta = _parse_heading_meta(content)

        # 表格源头的条目（产品清单/技术规格表中的数值编号）不应解析为章节标题
        # 它们已是产品节点下的规格子项，避免"12.以最小数值作为一条参数"等文本被错误提升为章节节点
        _is_table_source = source_section.startswith("table_classification.")
        if meta["kind"] == "numeric" and meta["codes"] and _is_table_source:
            meta = {"kind": "plain", "level": None, "codes": [], "heading": "", "body": content}

        if meta["kind"] == "numeric" and meta["codes"]:
            code = meta["codes"][-1]
            title = _make_node_title(code, meta.get("body", ""))
            if not title and not meta.get("body", "").strip():
                continue
            parent = None
            for ancestor_code in reversed(meta["codes"][:-1]):
                parent = numeric_nodes.get(ancestor_code)
                if parent is not None:
                    break
            node = numeric_nodes.get(code)
            if node is None:
                node = _create_tree_node(
                    f"tech-node-{node_seq[0]}",
                    title,
                    len(meta["codes"]),
                    source_section,
                    content,
                    clause_no=code,
                    package_no=package_no,
                )
                node_seq[0] += 1
                _append_child(parent, node)
                numeric_nodes[code] = node
            else:
                node["title"] = title or node.get("title", "")
                node["content"] = content or node.get("content", "")
                node["clause_no"] = code
            level_stack[len(meta["codes"])] = node
            for key in list(level_stack.keys()):
                if key > len(meta["codes"]):
                    level_stack.pop(key, None)
            continue

        level_hint = meta.get("level")
        if isinstance(level_hint, int):
            parent = level_stack.get(level_hint - 1)
        else:
            active_levels = [lvl for lvl in level_stack.keys() if lvl > 0]
            parent = level_stack.get(max(active_levels)) if active_levels else None
            level_hint = (parent.get("level", 0) + 1) if isinstance(parent, dict) else 1

        title = _make_node_title(meta.get("heading", ""), meta.get("body", content))
        if not title and not str(meta.get("body", "") or content).strip():
            continue
        node = _create_tree_node(
            f"tech-node-{node_seq[0]}",
            title or str(content)[:40],
            level_hint,
            source_section,
            content,
            clause_no=str(meta.get("heading", "")).strip(),
            package_no=package_no,
        )
        node_seq[0] += 1
        _append_child(parent, node)
        level_stack[level_hint] = node
        for key in list(level_stack.keys()):
            if key > level_hint:
                level_stack.pop(key, None)

    return roots


def _merge_product_rows(analysis: dict) -> list:
    """合并产品清单和技术规格表，形成产品级技术要求候选。"""
    tc = analysis.get("table_classification", {}) or {}
    if not isinstance(tc, dict):
        return []
    packages = analysis.get("packages", []) if isinstance(analysis, dict) else []

    merged = {}
    order = []
    package_bound_detected = False

    def _ensure_product(scope_key: str, name: str, source_section: str):
        key = f"{scope_key}::{name}"
        if key not in merged:
            merged[key] = {
                "scope_key": scope_key,
                "name": name,
                "source_section": source_section,
                "specifications": [],
                "remark": "",
            }
            order.append(key)
        return merged[key]

    for pkg in packages if isinstance(packages, list) else []:
        if not isinstance(pkg, dict):
            continue
        package_no = str(pkg.get("package_no", "")).strip()
        if not package_no:
            continue
        params = pkg.get("parameters") or {}
        if not isinstance(params, dict):
            continue
        scope_key = f"package:{package_no}"
        for row in params.get("product_table_items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            product = _ensure_product(scope_key, name, "table_classification.product")
            remark = str(row.get("remark", "")).strip()
            if remark:
                product["remark"] = remark
            package_bound_detected = True
        for row in params.get("tech_table_items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            spec = str(row.get("specification", "")).strip()
            if not name and not spec:
                continue
            package_bound_detected = True
            if name:
                product = _ensure_product(scope_key, name, "table_classification.tech")
                if spec:
                    product["specifications"].append(spec)
            else:
                key = f"{scope_key}::__anonymous__"
                if key not in merged:
                    merged[key] = {
                        "scope_key": scope_key,
                        "name": "",
                        "source_section": "table_classification.tech",
                        "specifications": [],
                        "remark": "",
                    }
                    order.append(key)
                if spec:
                    merged[key]["specifications"].append(spec)

    if package_bound_detected:
        return [merged[key] for key in order]

    for tbl in tc.get("product_lists", []) or []:
        if not isinstance(tbl, dict):
            continue
        for row in tbl.get("items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            text = " ".join(str(row.get(k, "")).strip() for k in ("name", "remark") if str(row.get(k, "")).strip())
            scope_key = _root_scope_for_text(text, analysis.get("packages", []) or [])
            product = _ensure_product(scope_key, name, "table_classification.product")
            remark = str(row.get("remark", "")).strip()
            if remark:
                product["remark"] = remark

    for tbl in tc.get("tech_requirements", []) or []:
        if not isinstance(tbl, dict):
            continue
        for row in tbl.get("items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            spec = str(row.get("specification", "")).strip()
            if not name and not spec:
                continue
            text = f"{name} {spec}".strip()
            scope_key = _root_scope_for_text(text, analysis.get("packages", []) or [])
            if name:
                product = _ensure_product(scope_key, name, "table_classification.tech")
                if spec:
                    product["specifications"].append(spec)
            else:
                key = f"{scope_key}::__anonymous__"
                if key not in merged:
                    merged[key] = {
                        "scope_key": scope_key,
                        "name": "",
                        "source_section": "table_classification.tech",
                        "specifications": [],
                        "remark": "",
                    }
                    order.append(key)
                if spec:
                    merged[key]["specifications"].append(spec)

    return [merged[key] for key in order]


def _append_requirement_children(parent: dict, texts: list, node_seq: list[int], source_section: str, package_no: str = "") -> None:
    """向父节点追加要求子树。"""
    items = [{"content": text, "source_section": source_section} for text in texts if str(text or "").strip()]
    if not items:
        return
    children = _build_group_tree(source_section, items, node_seq, package_no=package_no)
    parent.setdefault("children", []).extend(children)


def _build_technical_tree(items: list, analysis: dict, result) -> list:
    """基于包号/共享范围组织技术要求树。"""
    packages = _load_packages(result, analysis)
    scope_nodes = {}
    scope_order = []
    node_seq = [1]

    def _scope_node(scope_key: str) -> dict:
        return _ensure_scope_node(scope_nodes, scope_order, scope_key, packages)

    products = _merge_product_rows(analysis)
    product_scope_map = {}
    duplicate_product_names = set()
    for product in products:
        name = str(product.get("name", "")).strip()
        scope_key = str(product.get("scope_key", "")).strip()
        if not name or not scope_key:
            continue
        if name in product_scope_map and product_scope_map[name] != scope_key:
            duplicate_product_names.add(name)
        else:
            product_scope_map[name] = scope_key
    for name in duplicate_product_names:
        product_scope_map.pop(name, None)

    anonymous_specs = []
    for product in products:
        scope_key = product.get("scope_key") or _default_scope_key(packages)
        scope = _scope_node(scope_key)
        package_no = scope.get("package_no", "")
        name = str(product.get("name", "")).strip()
        specs = [spec for spec in product.get("specifications", []) if str(spec or "").strip()]
        if name:
            product_node = _create_tree_node(
                f"tech-node-{node_seq[0]}",
                name,
                2,
                product.get("source_section", "table_classification.tech"),
                "",
                node_type="product",
                package_no=package_no,
            )
            node_seq[0] += 1
            scope.setdefault("children", []).append(product_node)
            _append_requirement_children(
                product_node,
                specs or ([product.get("remark")] if product.get("remark") else []),
                node_seq,
                product.get("source_section", "table_classification.tech"),
                package_no=package_no,
            )
        else:
            anonymous_specs.extend(
                {
                    "scope_key": scope_key,
                    "source_section": product.get("source_section", "table_classification.tech"),
                    "text": spec,
                }
                for spec in specs
            )

    # 过滤 comprehensive 源中已被产品节点覆盖的条目（避免包级内容重复落入 shared_scope）
    _all_product_names = set()
    _all_scope_keys = set()
    for _name, _sk in product_scope_map.items():
        if _name and _sk:
            _all_product_names.add(_name)
            _all_scope_keys.add(_sk)
    if _all_product_names:
        _before = len(items)
        items = [
            itm for itm in items
            if not (
                str(itm.get("source_section", "")).strip() == "comprehensive"
                and any(
                    _pname and _pname in str(itm.get("content", ""))
                    for _pname in _all_product_names
                )
            )
        ]
        if len(items) < _before:
            logger.info("technical: 过滤了 %d 个 comprehensive 重复条目（已被产品节点覆盖）", _before - len(items))

    grouped_text_items = {}
    grouped_order = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content", "")).strip()
        if not text:
            continue
        scope_key = _root_scope_for_text(text, packages)
        if scope_key == "scope:shared":
            for product_name, product_scope_key in product_scope_map.items():
                if product_name and product_name in text:
                    scope_key = product_scope_key
                    break
        source_section = str(item.get("source_section", "")).strip() or "unknown"
        group_label = _source_group_title(source_section)
        group_key = f"{scope_key}::{group_label}::{source_section}"
        if group_key not in grouped_text_items:
            grouped_text_items[group_key] = {
                "scope_key": scope_key,
                "group_label": group_label,
                "source_section": source_section,
                "items": [],
            }
            grouped_order.append(group_key)
        grouped_text_items[group_key]["items"].append(item)

    for anon in anonymous_specs:
        anon_group_label = _source_group_title(anon["source_section"])
        group_key = f"{anon['scope_key']}::{anon_group_label}::{anon['source_section']}"
        if group_key not in grouped_text_items:
            grouped_text_items[group_key] = {
                "scope_key": anon["scope_key"],
                "group_label": anon_group_label,
                "source_section": anon["source_section"],
                "items": [],
            }
            grouped_order.append(group_key)
        grouped_text_items[group_key]["items"].append(
            {"content": anon["text"], "source_section": anon["source_section"]}
        )

    for group_key in grouped_order:
        group = grouped_text_items[group_key]
        scope = _scope_node(group["scope_key"])
        package_no = scope.get("package_no", "")
        group_node = _create_tree_node(
            f"tech-node-{node_seq[0]}",
            group["group_label"],
            2,
            group["source_section"],
            "",
            node_type="requirement_group",
            package_no=package_no,
        )
        node_seq[0] += 1
        children = _build_group_tree(
            group["source_section"],
            group["items"],
            node_seq,
            package_no=package_no,
        )
        if children:
            group_node["children"] = children
            scope.setdefault("children", []).append(group_node)

    roots = [scope_nodes[key] for key in scope_order if scope_nodes.get(key, {}).get("children")]
    _assign_tree_meta(roots)
    return _sanitize_tree_nodes(roots)


def _collect_from_comprehensive(analysis: dict, seen: set) -> list:
    """从 _comprehensive 结构化列表提取技术要求。"""
    items = []
    for tr in analysis.get("_comprehensive", {}).get("technical_requirements", []):
        text = tr.get("requirement", "").strip()
        _append_atomic_items(items, seen, text, "comprehensive")
    return items


def _collect_from_table_classification(_analysis: dict, _seen: set) -> list:
    """表格类技术项由产品树统一组织，这里不再额外平铺收集。"""
    return []


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
    """从 DB 顶层 technical_requirements 兜底提取当前树形结构。"""
    items = []
    tech_payload = result.technical_requirements
    if not tech_payload:
        return items
    parsed = None
    if isinstance(tech_payload, str):
        try:
            parsed = json.loads(tech_payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            return items
    elif isinstance(tech_payload, dict):
        parsed = tech_payload
    else:
        return items

    def _walk_tree_nodes(nodes):
        if not isinstance(nodes, list):
            return
        for entry in nodes:
            if not isinstance(entry, dict):
                continue
            text = entry.get("content", "") or (
                entry.get("title", "") if not (entry.get("children") or []) else ""
            )
            _append_atomic_items(items, seen, text, "db_fallback")
            _walk_tree_nodes(entry.get("children", []) or [])

    if not isinstance(parsed, dict):
        return items
    _walk_tree_nodes(parsed.get("items", []) or [])
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

    tree_items = _build_technical_tree(all_items, analysis, result)
    return {
        "scope_mode": _infer_scope_mode(tree_items),
        "items": tree_items,
    }


def assemble_technical(result, analysis: dict) -> dict:
    """兼容 check-items 的技术要求组装入口。"""
    return build_technical_section(result, analysis)
