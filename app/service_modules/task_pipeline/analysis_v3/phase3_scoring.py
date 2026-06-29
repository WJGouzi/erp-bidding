"""Phase 3: 得分点拆解 + 分包技术参数统计 — 纯规则，零LLM。

核心功能：
  1. extract_scoring(): 从文档中找评分表，表格优先→文本表格启发式→段落匹配
  2. extract_packages(): 对分包项目按包号统计 ★/▲/一般参数数量

支持三种评分表格式：
  - 原生DOCX表格（python-docx）
  - 纯文本表格（空格/制表符/| 对齐）
  - 段落描述（如"报价得分30分"）
"""

import json
import logging
import re


# ── 通用标题前置符剥离 ──
_HEADING_PREFIX_RE = re.compile(r'^[^\w\u4e00-\u9fff\d]+')


def _strip_heading_prefix(text: str) -> str:
    """剥离标题前导装饰字符，保留标题实质内容。"""
    if not text:
        return text
    return _HEADING_PREFIX_RE.sub('', text)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════
#  常量定义
# ══════════════════════════════════════════

SCORE_TABLE_HEADERS = {
    "name": ["评分因素", "评分项", "评分项目", "评审因素", "评审项目", "评审内容", "评分内容"],
    "score": ["分值", "分数", "权重", "标准分值", "标准分数", "权值"],
    "criteria": ["评分标准", "评审标准", "评审细则", "评分细则", "评审准则", "评标标准", "评分规则"],
    "rank": ["序号", "排名", "顺序", "编号"],
}

SUBJECTIVE_KEYWORDS = [
    "方案", "措施", "计划", "流程", "制度", "组织",
    "方法", "体系", "安排", "思路", "管理", "保障",
    "承诺", "服务方案", "技术方案",
]

OBJECTIVE_KEYWORDS = [
    "报价", "价格", "下浮率", "业绩",
]

TECH_KEYWORDS = ["技术", "规格", "参数", "型号", "配置"]


# ══════════════════════════════════════════
#  章节搜索
# ══════════════════════════════════════════

def _find_scoring_section(sections):
    """在 section 树中智能查找评分相关章节。

    策略：
      1. 先按章节标题精确匹配
      2. 再按内容评分（根据评分相关关键词密度打分，选最高分）
      3. 排除目录/前附表等干扰章节
    """
    targets = ["评标办法", "评分", "评审办法", "综合评分", "评分标准", "比选办法", "评审方法", "评审因素", "评审细则"]
    toc_keywords = ["目录", "TOC", "前附表", "投标邀请", "比选公告",
                    "比选须知", "响应文件格式", "合同模板"]

    def _section_score(node):
        """计算一个章节树的评分相关分值。"""
        score = 0
        title = getattr(node, "title", "") or ""
        stripped = _strip_heading_prefix(title)
        
        # 标题匹配加分（剥离前缀后匹配）
        for t in targets:
            if t in title or t in stripped:
                score += 10
        # 标题含目录关键词减分
        for t in toc_keywords:
            if t in title or t in stripped:
                score -= 5
        
        # 内容匹配加分
        full_text_parts = []
        for block in getattr(node, "content", []):
            text = getattr(block, "text", "") or ""
            full_text_parts.append(text)
        for child in getattr(node, "children", []):
            full_text_parts.append(_get_all_text(child))
        
        full_text = " ".join(full_text_parts)
        
        for t in ["评分因素", "分值", "评分标准", "评审细则",
                   "评分因素及权重", "综合评分明细表"]:
            if t in full_text:
                score += 15
        
        for t in targets:
            if t in full_text and t not in (getattr(node, "title", "") or ""):
                score += 3
        
        # 内容长度加分（说明有实际内容，不是空壳章节）
        if len(full_text) > 50:
            score += 2
        if len(full_text) > 200:
            score += 3
        
        return score

    def _get_all_text(node):
        texts = []
        t = getattr(node, "title", "") or ""
        if t:
            texts.append(t)
        for b in getattr(node, "content", []):
            if getattr(b, "text", ""):
                texts.append(b.text)
            elif b.type == "table":
                parts = []
                if b.headers:
                    parts.append(" | ".join(b.headers))
                for row in b.rows:
                    parts.append(" | ".join(row))
                if parts:
                    texts.append("\n".join(parts))
        for c in getattr(node, "children", []):
            texts.append(_get_all_text(c))
        return "\n".join(texts)

    # 评分所有章节
    best_section = None
    best_score = -999

    def _score_tree(node):
        nonlocal best_section, best_score
        s = _section_score(node)
        if s > best_score:
            best_score = s
            best_section = node
        for child in getattr(node, "children", []):
            _score_tree(child)

    for section in sections:
        _score_tree(section)

    return best_section if best_score > 0 else None

    for section in sections:
        result = _search_title(section)
        if result:
            return result

    # 阶段2：内容搜索（排除目录相关章节）
    toc_keywords = ["目录", "TOC", "前附表", "投标邀请"]

    def _search_content(node):
        title = getattr(node, "title", "") or ""
        # 跳过目录相关章节
        for toc_kw in toc_keywords:
            if toc_kw in title:
                return None
        for block in getattr(node, "content", []):
            text = getattr(block, "text", "") or ""
            for t in targets:
                if t in text:
                    return node
        for child in getattr(node, "children", []):
            result = _search_content(child)
            if result:
                return result
        return None

    for section in sections:
        result = _search_content(section)
        if result:
            return result

    return None


# 技术参数表的通用表头关键词
TECH_TABLE_HEADERS = [
    "序号",
    "品名", "产品名称", "名称", "标的名称",
    "规格型号", "规格", "型号",
    "数量", "数量（计量单位）",
    "单位", "计量单位",
    "技术参数", "技术指标",
    "单价", "总价", "最高限价",
]


def _detect_tech_table(headers, rows):
    """探测表格是否为技术参数表（通用表头关键词匹配）。
    
    检测标准：表头中至少 2 列匹配 TECH_TABLE_HEADERS。
    """
    if not headers:
        return False
    header_text = " ".join(h.lower()[:15] for h in headers)
    matched = sum(1 for kw in TECH_TABLE_HEADERS if kw.lower() in header_text)
    return matched >= 2


def _parse_tech_table(headers, rows):
    """将技术参数表行解析为结构化条目。"""
    items = []
    for row in rows:
        cells = [cell.text.strip() if hasattr(cell, 'text') else str(cell) for cell in row]
        entry = {}
        for i, h in enumerate(headers):
            entry[h] = cells[i] if i < len(cells) else ""
        items.append(entry)
    return items


def _find_tech_section(sections):
    """加强版：标题匹配 → 内容表格探测 两阶段。
    
    Phase 1: 标题匹配（现有逻辑）
    Phase 2: 内容表格探测（新增 fallback）
    """
    targets = ["技术要求", "技术参数", "技术规格", "技术标准", "采购需求", "需求一览表", "采购项目技术", "比选项目及要求", "项目及要求", "采购项目"]
    
    # Phase 1: 标题匹配（剥离前缀后匹配 ★◆● 等标记）
    for section in sections:
        title = getattr(section, "title", "") or ""
        stripped = _strip_heading_prefix(title)
        for t in targets:
            if t in title or t in stripped:
                return section
        for child in getattr(section, "children", []):
            child_title = getattr(child, "title", "") or ""
            child_stripped = _strip_heading_prefix(child_title)
            for t in targets:
                if t in child_title or t in child_stripped:
                    return child
    
    # Phase 2: 内容表格探测（标题未匹配时检测表格内容）
    for section in sections:
        for block in getattr(section, "content", []):
            if getattr(block, "type", "") == "table":
                headers = getattr(block, "headers", []) or []
                rows = getattr(block, "rows", []) or []
                if _detect_tech_table(headers, rows):
                    return section
        for child in getattr(section, "children", []):
            for block in getattr(child, "content", []):
                if getattr(block, "type", "") == "table":
                    headers = getattr(block, "headers", []) or []
                    rows = getattr(block, "rows", []) or []
                    if _detect_tech_table(headers, rows):
                        return child
    
    return None


def _find_package_sections(source_sections, package_nos):
    """在各包对应的子章节中查找。"""
    pkg_map = {pkg_no: None for pkg_no in package_nos}

    def _find_by_title(node, title_keyword):
        title = getattr(node, "title", "") or ""
        # 剥离前缀后匹配（★第1包 → 第1包）
        if title_keyword in title or title_keyword in _strip_heading_prefix(title):
            return node
        for child in getattr(node, "children", []):
            result = _find_by_title(child, title_keyword)
            if result:
                return result
        return None

    for section in source_sections:
        for pkg_no in package_nos:
            keyword = f"第{pkg_no}包"
            if pkg_map.get(pkg_no) is None:
                found = _find_by_title(section, keyword)
                if found:
                    pkg_map[pkg_no] = found

    return pkg_map


# ══════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════

def _section_to_text(section):
    """将 section 及其子节点转为纯文本。"""
    texts = []
    title = getattr(section, "title", "") or ""
    if title:
        texts.append(title)
    for block in getattr(section, "content", []):
        if block.type in ("paragraph", "heading", "list"):
            if getattr(block, "text", ""):
                texts.append(block.text)
        elif block.type == "table":
            parts = []
            if block.headers:
                parts.append(" | ".join(block.headers))
            for row in block.rows:
                parts.append(" | ".join(row))
            if parts:
                texts.append("\n".join(parts))
    for child in getattr(section, "children", []):
        child_text = _section_to_text(child)
        if child_text:
            texts.append(child_text)
    return "\n".join(texts)


def _find_tables_in_section(section):
    """递归找 section 下所有 ContentBlock 中 type=table 的。"""
    tables = []
    for block in getattr(section, "content", []):
        if getattr(block, "type", "") == "table":
            tables.append(block)
    for child in getattr(section, "children", []):
        tables.extend(_find_tables_in_section(child))
    return tables


def _find_col_index(headers, candidates):
    """在表头行中找匹配的列索引。"""
    for i, h in enumerate(headers):
        h_clean = h.strip()
        for c in candidates:
            if c in h_clean:
                return i
    return None


def _extract_number(text):
    """从 '30分' 或 '30' 中提取数字。"""
    m = re.search(r"(\d+\.?\d*)", text)
    return float(m.group(1)) if m else 0


def _detect_score_type(name, criteria=""):
    """检测评分类型：objective / subjective / semi_objective。"""
    name_lower = (name or "").lower()
    criteria_lower = (criteria or "").lower()

    # 报价类 → objective
    for kw in OBJECTIVE_KEYWORDS:
        if kw in name_lower:
            return "objective"

    # 技术规格类 → semi_objective
    has_tech = any(kw in name_lower for kw in TECH_KEYWORDS)
    if has_tech:
        return "semi_objective"

    # 方案类 → subjective
    for kw in SUBJECTIVE_KEYWORDS:
        if kw in name_lower or kw in criteria_lower:
            return "subjective"

    # 默认
    return "subjective"


def _extract_sub_dimensions(criteria_str):
    """从评分标准中提取子维度。"""
    if not criteria_str or len(criteria_str) < 5:
        return []

    sub_dims = []

    # 模式1: "1. xxx 2. xxx"
    nums = re.findall(r"(\d+)[、.．\s]\s*([^。\d]+)", criteria_str)
    for num, desc in nums:
        desc = desc.strip()
        if len(desc) > 2:
            sub_dims.append({"name": desc[:60], "description": desc[:120]})

    # 模式2: "（一）xxx（二）xxx"
    cns = re.findall(r"（[一二三四五六七八九十]+）\s*([^。]+)", criteria_str)
    for desc in cns:
        desc = desc.strip()
        if len(desc) > 2:
            sub_dims.append({"name": desc[:60], "description": desc[:120]})

    return sub_dims


# ══════════════════════════════════════════
#  纯文本表格检测
# ══════════════════════════════════════════

def _is_text_table_line(line):
    """判断一行是否是文本表格的一部分。
    
    检测条件（满足任一即可）：
      1. 包含 "|" 分隔符
      2. 包含 "\t" 制表符
      3. 以制表符字符开头（┌┐└┘├┤┬┴┼═等）
      4. 连续多行空格对齐（启发式）
    """
    if not line or not line.strip():
        return False

    # 制表符字符
    box_chars = "┌┐└┘├┤┬┴┼═─│┃╔╗╚╝╠╣╦╩╬"
    if line[0] in box_chars:
        return True

    if "│" in line or "|" in line:
        return True

    if "\t" in line:
        return True

    return False


def _detect_text_tables(section_text):
    """从纯文本中检测并提取表格结构。

    返回 list of dict: [{"headers": [...], "rows": [[...], ...]}]
    """
    lines = section_text.split("\n")
    tables = []
    current_table_lines = []
    in_table = False

    for line in lines:
        if _is_text_table_line(line):
            # 跳过纯分隔线（┌───┬───┐ 等）
            stripped = line.strip()
            if all(c in "┌┐└┘├┤┬┴┼═─│┃╔╗╚╝╠╣╦╩╬ " for c in stripped):
                continue
            if all(c == "-" or c == " " or c == "|" or c == "+" for c in stripped):
                continue
            current_table_lines.append(line)
            in_table = True
        else:
            if in_table and len(current_table_lines) >= 2:
                table = _parse_text_table(current_table_lines)
                if table:
                    tables.append(table)
            current_table_lines = []
            in_table = False

    if in_table and len(current_table_lines) >= 2:
        table = _parse_text_table(current_table_lines)
        if table:
            tables.append(table)

    return tables


def _parse_text_table(lines):
    """从文本表格行中解析表头和行数据。"""
    if not lines:
        return None

    # 处理 "|" 分隔的表格
    pipe_lines = [l for l in lines if "|" in l or "│" in l]
    if pipe_lines:
        # 去除分隔线（|---|----|）
        data_lines = [l for l in pipe_lines if not re.match(r"^[\s\|│\-━═─]+$", l)]
        if len(data_lines) < 2:
            return None

        parsed_rows = []
        for line in data_lines:
            cells = [c.strip() for c in re.split(r"[|│]", line) if c.strip()]
            if cells:
                parsed_rows.append(cells)

        if len(parsed_rows) >= 2:
            return {
                "headers": parsed_rows[0],
                "rows": parsed_rows[1:],
            }

    # 处理 "\t" 分隔的表格
    tab_lines = [l for l in lines if "\t" in l]
    if tab_lines:
        parsed_rows = []
        for line in tab_lines:
            cells = [c.strip() for c in line.split("\t") if c.strip()]
            if cells:
                parsed_rows.append(cells)

        if len(parsed_rows) >= 2:
            return {
                "headers": parsed_rows[0],
                "rows": parsed_rows[1:],
            }

    return None


# ══════════════════════════════════════════
#  评分表解析
# ══════════════════════════════════════════

def parse_scoring_table(table_block):
    """从 ContentBlock（type=table）中解析评分维度。

    支持灵活的表头匹配，不要求固定表头顺序。
    """
    headers = getattr(table_block, "headers", []) or []
    rows = getattr(table_block, "rows", []) or []

    if not headers or not rows:
        return []

    # 找各列索引
    name_idx = _find_col_index(headers, SCORE_TABLE_HEADERS["name"])
    score_idx = _find_col_index(headers, SCORE_TABLE_HEADERS["score"])
    criteria_idx = _find_col_index(headers, SCORE_TABLE_HEADERS["criteria"])
    rank_idx = _find_col_index(headers, SCORE_TABLE_HEADERS["rank"])

    if name_idx is None:
        # 没有明确的"评分因素"列，尝试用序号列或第一列
        if rank_idx is not None:
            name_idx = rank_idx
        else:
            name_idx = 0

    if score_idx is None:
        # 没有分值列，尝试用最后一列
        score_idx = len(headers) - 1

    dimensions = []

    for row in rows:
        if len(row) <= max(name_idx, score_idx) if score_idx else len(row) <= 1:
            continue

        name = row[name_idx].strip() if name_idx < len(row) else ""
        if not name:
            continue

        score = _extract_number(row[score_idx]) if score_idx < len(row) else 0
        if score == 0:
            continue

        criteria = row[criteria_idx].strip() if criteria_idx is not None and criteria_idx < len(row) else ""

        score_type = _detect_score_type(name, criteria)
        sub_dims = _extract_sub_dimensions(criteria)

        dim = {
            "name": name[:60],
            "score": int(score) if score == int(score) else score,
            "type": score_type,
        }

        if sub_dims:
            dim["sub_dimensions"] = sub_dims[:5]

        dimensions.append(dim)

    return dimensions


def _parse_scoring_from_text(section_text):
    """从纯文本中解析评分维度（降级方案，当无表格时使用）。

    匹配模式：
      - "xxx（yyy分）" 或 "xxx(yyy分)"
      - "xxx得（满）分yyy分"
      - "评分因素：xxx；分值：yyy"
      - 逐行关键词匹配
    """
    dimensions = []

    # 模式1: "xxx(30分)" 或 "xxx（30分）"
    patterns = [
        r"([^。，；\d]{2,20})[（(](\d+\.?\d*)\s*分[）)]",
        r"([^。，；\d]{2,20})[：:]\s*(\d+\.?\d*)\s*分",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, section_text):
            name = match.group(1).strip()
            score = float(match.group(2))
            if name and score > 0:
                # 去重
                if not any(d["name"] == name[:60] for d in dimensions):
                    score_type = _detect_score_type(name)
                    dimensions.append({
                        "name": name[:60],
                        "score": int(score) if score == int(score) else score,
                        "type": score_type,
                    })

    # 如果还没找到，尝试逐行关键词匹配
    if not dimensions:
        lines = section_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # "报价 30分" 或 "技术方案 20分"
            m = re.search(r"([\u4e00-\u9fff]{2,10})\s+(\d+\.?\d*)\s*[分分]", line)
            if m:
                name = m.group(1).strip()
                score = float(m.group(2))
                if name and score > 0:
                    if not any(d["name"] == name[:60] for d in dimensions):
                        score_type = _detect_score_type(name)
                        dimensions.append({
                            "name": name[:60],
                            "score": int(score) if score == int(score) else score,
                            "type": score_type,
                        })

    return dimensions


def _detect_scoring_method(section_text):
    """检测评分方法。"""
    if "综合评分" in section_text or "综合评估" in section_text:
        return "comprehensive"
    if "最低评标价" in section_text:
        return "lowest_price"
    return "comprehensive"


# ══════════════════════════════════════════
#  包参数统计（零LLM）
# ══════════════════════════════════════════

def _count_pkg_params(text):
    if not text:
        return 0, 0, 0
    import re
    starred = len(re.findall(r"[\u2605\u2b50]", text))
    important = len(re.findall(r"[\u25b2]", text))
    param_patterns = re.findall(
        r"(?:\u53c2\u6570|\u89c4\u683c|\u578b\u53f7|\u914d\u7f6e|\u6280\u672f\u6307\u6807)[^\u3002]*?[\uff1a:][^\u3002]*?(?:\d+[.\uff0e]\d+|[\u4e00-\u9fff])",
        text)
    general = len(param_patterns)
    return starred, important, general


def _detect_core_products(text):
    if not text:
        return []
    products = []
    import re
    for m in re.finditer(r"(?:\u6838\u5fc3\u4ea7\u54c1|\u4e3b\u8981\u4ea7\u54c1)[\uff1a:]*\s*([^\u3002\uff1b]+)", text):
        products.append(m.group(1).strip()[:60])
    return products[:10]








def extract_scoring(sections):
    """从文档中提取评分维度——纯规则，零LLM。"""
    scoring_section = _find_scoring_section(sections)
    if not scoring_section:
        logger.warning("[phase3] 未找到评分章节")
        return {"method": "", "total_score": 0, "dimensions": []}

    dimensions = []
    tables = _find_tables_in_section(scoring_section)
    for table in tables:
        dims = parse_scoring_table(table)
        if dims:
            dimensions.extend(dims)

    section_text = _section_to_text(scoring_section)
    text_tables = _detect_text_tables(section_text)
    for text_table in text_tables:
        fake_headers = text_table["headers"]
        from collections import namedtuple
        FakeBlock = namedtuple("FakeBlock", ["headers", "rows"])
        fake_block = FakeBlock(headers=fake_headers, rows=text_table["rows"])
        dims = parse_scoring_table(fake_block)
        if dims:
            for d in dims:
                if not any(x["name"] == d["name"] for x in dimensions):
                    dimensions.append(d)

    if not dimensions:
        dimensions = _parse_scoring_from_text(section_text)

    method = _detect_scoring_method(section_text)

    seen_names = set()
    unique_dims = []
    for d in dimensions:
        if d["name"] not in seen_names:
            seen_names.add(d["name"])
            unique_dims.append(d)

    total_score = sum(d.get("score", 0) for d in unique_dims)
    return {"method": method, "total_score": total_score, "dimensions": unique_dims}


def extract_packages(sections, package_nos, metadata_budget=None, pkg_name_map=None, table_results=None):
    """按包分析技术参数——纯规则，零LLM。"""
    if not package_nos:
        return []

    tech_section = _find_tech_section(sections)
    source_sections = [tech_section] if tech_section else sections
    pkg_section_map = _find_package_sections(source_sections, package_nos)

    budget_per_pkg = {}
    budget_total = 0
    if metadata_budget and isinstance(metadata_budget, dict):
        budget_per_pkg = metadata_budget.get("packages", {})
        budget_total = metadata_budget.get("total", 0) or 0

    packages = []
    for pkg_no in package_nos:
        section = pkg_section_map.get(pkg_no)
        if not section:
            # 单包场景：使用 tech_section 或 source_sections[0] 回退
            if not (len(package_nos) == 1 and source_sections):
                # 多包场景下未找到包章节，使用兜底名
                pkg_fallback = (pkg_name_map or {}).get(pkg_no, "") or f"第{pkg_no}包"
                pkg_entry = {
                    "package_no": pkg_no,
                    "name": pkg_fallback,
                    "budget": budget_per_pkg.get(str(pkg_no), budget_total) if budget_per_pkg else budget_total,
                    "parameters": None,
                }
                pkg_entry["strategy"] = analyze_package_strategy(pkg_entry)
                packages.append(pkg_entry)
                continue
            # 单包场景：使用 source_section 作为回退
            section = source_sections[0]
            logger.info("[phase3] 单包场景，使用 source_section 作为包 %s 的章节: %s",
                        pkg_no, getattr(section, 'title', '') or '全文')

        # 包名优先级：pkg_name_map（从原文提取）> 章节标题 > 兜底名
        # 包名优先级：pkg_name_map（从原文提取） > 仅用于单包且有意义的章节标题 > 兜底名
        pkg_name = (pkg_name_map or {}).get(pkg_no, "") or f"第{pkg_no}包"
        # 单包场景：明确提取到有意义的章节标题才作为包名
        if not (pkg_name_map or {}).get(pkg_no):
            section_title = getattr(section, "title", "") or ""
            # 4个条件同时满足才使用章节标题：单包、标题短、不含"第X章"、不含"第X部分"
            if (len(package_nos) == 1 and 
                section_title and len(section_title) < 20 and
                not re.match(r'^第[一二三四五六七八九十零〇百千万亿]+', section_title) and
                not re.match(r'^第\d+[章节部篇]', section_title)):
                pkg_name = section_title
            else:
                pkg_name = ""
        pkg_text = _section_to_text(section)
        starred, important, general = _count_pkg_params(pkg_text)
        core_products = _detect_core_products(pkg_text)
        params = {
            "starred_count": starred,
            "important_count": important,
            "general_count": general,
            "core_products": core_products,
            "online_platform_items": ("挂网" in pkg_text or "药械" in pkg_text),
        }
        pkg_entry = {
            "package_no": pkg_no,
            "name": pkg_name,
            "budget": budget_per_pkg.get(str(pkg_no), budget_total) if budget_per_pkg else budget_total,
            "parameters": params,
        }
        # 融合表格分类结果中的产品清单数据
        if table_results and params.get("table_items") is None:
            try:
                classification = table_results.get("_classification", {})
                for pl in classification.get("product_lists", []):
                    items = pl.get("items", [])
                    if items:
                        pkg_entry["parameters"]["table_items"] = items
            except Exception:
                pass
        pkg_entry["strategy"] = analyze_package_strategy(pkg_entry)
        packages.append(pkg_entry)

    return packages


def split_content_by_package(sections, package_nos):
    pkg_content = {pkg_no: [] for pkg_no in package_nos}
    pkg_content["shared"] = []

    if not package_nos:
        pkg_content["shared"].extend(sections)
        return pkg_content

    def _get_pkg_no(text):
        if not text:
            return None
        import re
        m = re.search(r"第(\d+)包[：：\s]", text)
        if m:
            return int(m.group(1))
        m = re.search(r"(?:采购)?包(\d+)[：：\s）)]", text)
        if m:
            return int(m.group(1))
        m = re.search(r"[（(]采购包(\d+)[）)]", text)
        if m:
            return int(m.group(1))
        return None

    def _assign_section(section):
        title = getattr(section, "title", "") or ""
        pkg_no = _get_pkg_no(title)
        if pkg_no and pkg_no in package_nos:
            pkg_content[pkg_no].append(section)
        else:
            children = getattr(section, "children", [])
            if children:
                has_pkg_child = False
                for child in children:
                    child_pkg = _get_pkg_no(getattr(child, "title", "") or "")
                    if child_pkg and child_pkg in package_nos:
                        pkg_content[child_pkg].append(child)
                        has_pkg_child = True
                    else:
                        _assign_section(child)
                if not has_pkg_child:
                    pkg_content["shared"].append(section)
            else:
                pkg_content["shared"].append(section)

    for section in sections:
        _assign_section(section)
    return pkg_content


def analyze_package_strategy(pkg_data):
    params = pkg_data.get("parameters") or {}
    name = pkg_data.get("name", "")
    starred = params.get("starred_count", 0)
    important = params.get("important_count", 0)
    
    difficulty = "low"
    risk_factors = []
    danger_kws = ["危", "毒", "爆", "炸", "压缩", "液化", "医疗", "器械"]
    for kw in danger_kws:
        if kw in name:
            difficulty = "high"
            risk_factors.append(f"need_special_permit:{kw}")
            break
    
    if starred > 10:
        difficulty = "high"
        risk_factors.append(f"starred_clauses:{starred}")
    
    competition = "medium"
    supplier_count = pkg_data.get("supplier_count", 0)
    if supplier_count >= 5:
        competition = "high"
    elif supplier_count <= 2:
        competition = "low"
    
    focus_parts = []
    if starred > 0:
        focus_parts.append(f"starred_response({starred}items)")
    if important > 0:
        focus_parts.append(f"important_response({important}items)")
    if params.get("core_products"):
        focus_parts.append(f"core_products({len(params['core_products'])}items)")
    
    return {
        "difficulty": difficulty,
        "competition": competition,
        "focus": "; ".join(focus_parts) if focus_parts else "standard_response",
        "risk": "; ".join(risk_factors) if risk_factors else "no_significant_risk",
    }


def cross_package_analysis(packages):
    result = {}
    if not packages or len(packages) < 2:
        return result
    scored = []
    for pkg in packages:
        score = 0
        budget_val = pkg.get("budget", 0) or 0
        if budget_val:
            score += min(budget_val / 10000, 10)
        params = pkg.get("parameters") or {}
        if params.get("starred_count", 0):
            score += 3
        if params.get("important_count", 0):
            score += 1
        scored.append((score, pkg))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        result["highest_value"] = scored[0][1].get("name", "")
    low_risk = None
    for pkg in packages:
        strat = pkg.get("strategy", {})
        if strat.get("difficulty") == "low" and strat.get("competition") != "high":
            low_risk = pkg
    if low_risk:
        result["lowest_risk"] = low_risk.get("name", "")
    recs = []
    if result.get("highest_value"):
        recs.append(f"priority:{result['highest_value']}")
    if result.get("lowest_risk"):
        recs.append(f"prefer:{result['lowest_risk']}")
    if recs:
        result["recommendations"] = " | ".join(recs)
    return result
