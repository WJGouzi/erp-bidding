"""Phase 1.5: 格式要求提取 — 从文档中提取响应文件的格式要求。

定位：
  Phase 1 (元数据) → Phase 1.5 (格式要求) → Phase 2 (资格) → Phase 3 (评分)

提取内容：
  - 格式要求章节（如"第三章 比选申请文件格式"）
  - 必选章节清单（响应函、报价一览表、授权书等）
  - 模板表格（固定的表格结构，如报价表模板）
  - 固定文本（必须出现的文字，如响应函声明文字）

使用方式：
    from .phase1_5_format import extract_format_requirements
    fmt_req = extract_format_requirements(doc.sections)
"""

import logging
import re
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  格式章节标题关键词（按优先级排序）
# ═══════════════════════════════════════════════════════════════

FORMAT_CHAPTER_KEYWORDS = [
    "比选申请文件格式",
    "投标文件格式",
    "响应文件格式",
    "申请文件格式",
    "比选申请文件",
    "投标文件",
    "响应文件格式要求",
    "文件格式",
]

# 格式章节中常见的必选文件标题（用于识别子章节）
REQUIRED_SECTION_PATTERNS = [
    # (关键词, 文件类型标识)
    (r"响应函|投标函|报价函", "response_letter"),
    (r"报价一览表|报价表|报价单|分项报价", "price_list"),
    (r"法定代表人授权|法人授权|授权委托", "authorization"),
    (r"资格证明|资质证明|资格文件", "qualification"),
    (r"实质性要求|★|实质性要求响应", "compliance"),
    (r"技术参数|技术响应|技术规格|技术要求响应", "technical"),
    (r"商务要求响应|商务条款", "business"),
    (r"评分标准|评分响应|综合评分", "scoring_response"),
    (r"售后服务|培训方案|服务方案", "service"),
    (r"项目业绩|类似项目|业绩证明", "performance"),
    (r"其他材料|其他文件|补充材料", "other"),
]


def _find_format_chapter(sections) -> Optional[object]:
    """在文档章节树中定位格式要求章节。

    策略：
      1. 按 FORMAT_CHAPTER_KEYWORDS 标题匹配
      2. 匹配后检查子章节数量（至少 3 个才视为有效格式章节）
    """
    best = None
    best_kw = ""

    def _search(section_list):
        nonlocal best, best_kw
        for section in section_list:
            title = getattr(section, "title", "") or ""
            for kw in FORMAT_CHAPTER_KEYWORDS:
                if kw in title:
                    children = getattr(section, "children", [])
                    if len(children) >= 2 or kw == best_kw:
                        best = section
                        best_kw = kw
                    break
            children = getattr(section, "children", [])
            if children:
                _search(children)

    _search(sections if isinstance(sections, list) else [])
    return best


def _extract_required_sections(section) -> List[Dict]:
    """从格式章节中提取必选文件清单。

    关键改进:
      1. 章节-内容绑定: 每个章节的 template_content **仅包含该章节直接归属**的内容块，
         子章节的内容仅在子章节自身生成条目，不上浮到父章节。
      2. 顺序保留: template_content 中的段落和表格严格按 Section.content 中的出场顺序排列。
      3. 章节层级: 子章节也会作为独立条目展开到 required 列表，保持层级可追溯。
      4. each section's own content is separately bound - tables/text don't leak across sections.

    Returns:
        List[Dict]: [{"title": ..., "required": True, "has_template": False, 
                       "order": 1, "template_content": [...], "children": [...]}, ...]
    """
    required = []
    children = getattr(section, "children", [])

    for idx, child in enumerate(children):
        title = getattr(child, "title", "") or ""
        if not title:
            continue

        # 检查是否有模板表格 → 仅检查当前章节的直接内容
        has_template = False
        for block in getattr(child, "content", []):
            if getattr(block, "type", "") == "table":
                has_template = True
                break
        # 递归检查子章节的模板表格（仅用于标记父章节有模板）
        if not has_template:
            for sub in getattr(child, "children", []):
                if _check_descendant_has_template(sub, False):
                    has_template = True
                    break

        # 识别文件类型
        file_type = "unknown"
        for pattern, ftype in REQUIRED_SECTION_PATTERNS:
            if re.search(pattern, title):
                file_type = ftype
                break

        # 提取固定文本内容
        template_texts = []
        # 有序内容列表：仅包含当前章节直接归属的内容块
        # 子章节的内容由子章节自己的条目管理
        template_content = []
        for block in getattr(child, "content", []):
            txt = getattr(block, "text", None)
            if txt and txt.strip() and len(txt.strip()) >= 5:
                template_texts.append(txt.strip())
            _type = getattr(block, "type", "") or ""
            if _type == "table":
                _headers = getattr(block, "headers", []) or []
                _rows = getattr(block, "rows", []) or []
                # 跳过完全空的表
                if not _headers and not _rows:
                    continue
                template_content.append({
                    "type": "table",
                    "headers": _headers,
                    "rows": _rows,
                    "merge_cells": getattr(block, "merge_cells", []),
                    "column_widths": getattr(block, "column_widths", []),
                    "per_cell": getattr(block, "per_cell_data", None) or _build_per_cell(
                        getattr(block, "headers", []),
                        getattr(block, "rows", []),
                        getattr(block, "merge_cells", []),
                        getattr(block, "column_widths", []),
                    ),
                })
            elif txt and txt.strip() and len(txt.strip()) >= 5:
                template_content.append({
                    "type": "text",
                    "text": txt.strip()[:2000],
                })

        # 递归处理子章节：子章节的内容归属到子章节自身，不上浮到父章节
        child_sub_entries = _extract_required_sections(child)

        required.append({
            "title": title,
            "order": idx + 1,
            "required": True,
            "has_template": has_template,
            "template_tables": _extract_template_tables(child),
            "template_texts": template_texts,
            "template_content": template_content,
            "file_type": file_type,
            "children": child_sub_entries,
        })

        # 子章节作为独立条目也加入 required（保持递归展开）
        required.extend(child_sub_entries)

    return required




def _check_descendant_has_template(section, current_has_template: bool) -> bool:
    """递归检查子章节及当前章节自身是否包含模板表格。

    先检查当前章节的 content（自身表格），再递归检查子章节（后代表格）。
    用于 _extract_required_sections 中检测父章节是否应标记 has_template=True。

    Args:
        section: 当前章节对象
        current_has_template: 当前已确定的模板标记

    Returns:
        bool: 当前或任意子章节有模板时返回 True
    """
    if current_has_template:
        return True
    # 1. 检查当前章节自身的 content
    for block in getattr(section, "content", []):
        if getattr(block, "type", "") == "table":
            return True
    # 2. 递归检查子章节
    for sub in getattr(section, "children", []):
        if _check_descendant_has_template(sub, False):
            return True
    return False


def _collapse_merged_columns(headers: List[str], rows: List[List[str]], cell_index: int = 0) -> tuple:
    """根据表头行中连续重复的列名折叠合并列（处理 WPS 伪合并）。

    WPS 表格经常出现相邻列有相同文本但无标准 OOXML hMerge 的情况，
    表现为 gridSpan=2 但两个物理格都存在且文本相同。
    此函数将连续相同文本的列合并为一个逻辑列。

    Args:
        headers: 表头列表
        rows: 数据行列表
        cell_index: 当 headers 长度与行单元格数不一致时尝试的索引偏移

    Returns:
        (collapsed_headers, collapsed_rows)
    """
    if not headers:
        return headers, rows

    # 检测需要折叠的列：连续相同文本的列组
    collapse_groups = []  # [(start, end), ...]
    i = 0
    while i < len(headers):
        j = i + 1
        while j < len(headers) and headers[j] == headers[i] and headers[i] != '':
            j += 1
        if j - i > 1:
            collapse_groups.append((i, j - 1))  # start, end inclusive
        i = j

    if not collapse_groups:
        return headers, rows

    # 构建旧列→新列的映射
    col_map = {}
    new_idx = 0
    for i in range(len(headers)):
        # 如果此列属于折叠组且不是第一个，映射到前一个
        in_group = False
        for start, end in collapse_groups:
            if start < i <= end:
                col_map[i] = col_map.get(start, new_idx - 1)
                in_group = True
                break
        if not in_group:
            col_map[i] = new_idx
            new_idx += 1

    # 折叠表头
    new_headers = []
    seen = set()
    for i, h in enumerate(headers):
        nc = col_map.get(i, i)
        if nc not in seen:
            new_headers.append(h)
            seen.add(nc)

    # 折叠数据行
    new_rows = []
    for row in rows:
        new_row = [''] * len(new_headers)
        for i, cell in enumerate(row):
            nc = col_map.get(i, i)
            if nc < len(new_row):
                if not new_row[nc]:
                    new_row[nc] = cell
                elif cell and cell != new_row[nc]:
                    # 非空内容不同时追加
                    new_row[nc] += ' ' + cell
        new_rows.append(new_row)

    return new_headers, new_rows


def _extract_template_tables(section) -> List[Dict]:
    """从章节中提取模板表格。

    仅搜索当前章节的直接内容块，**不递归子章节**。
    通过限制作用域防止解析器分组错误的表被错误关联。

    merge_cells 优先从 per_cell_data 读取（保留原始 XML 合并信息），
    降级到 block.merge_cells 属性（可能来自 _rebuild_merge_cells）。
    """
    tables = []
    
    for block in getattr(section, "content", []):
        if getattr(block, "type", "") == "table":
            headers = getattr(block, "headers", []) or []
            rows = getattr(block, "rows", []) or []
            if not headers and not rows:
                continue
            # 折叠水平合并列（处理 WPS 伪合并）
            headers, rows = _collapse_merged_columns(headers, rows)
            # 优先从 per_cell_data 读取原始 merge_cells
            _pcd = getattr(block, "per_cell_data", None)
            if _pcd and isinstance(_pcd, dict):
                _mc = _pcd.get("merge_cells", []) or []
            else:
                _mc = getattr(block, "merge_cells", []) or []
            tables.append({
                "headers": headers,
                "rows": rows,
                "merge_cells": _mc,
            })
    return tables



def _detect_cover_sections(sections) -> List[Dict]:
    """检测所有章节中的封面页。

    遍历整个文档章节树，搜索标题中包含"封面"或"封皮"的章节。
    封面页可能出现在文档树的任意位置（顶层级、格式章节子级等），
    需要全局搜索而非仅局限于格式章节。
    """
    covers = []
    
    def _search(section_list):
        for child in section_list:
            title = getattr(child, "title", "") or ""
            if "封面" in title or "封皮" in title:
                lines = []
                for block in getattr(child, "content", []):
                    text = getattr(block, "text", "") or ""
                    if text.strip():
                        lines.append(text.strip())
                covers.append({
                    "title": title,
                    "template_text": "\n".join(lines)[:1000],
                    "order": len(covers) + 1,
                })
            children = getattr(child, "children", [])
            if children:
                _search(children)
    
    _search(sections if isinstance(sections, list) else [])
    return covers


def _extract_fixed_texts(section) -> List[Dict]:
    """从章节中提取固定文本要求。

    一些格式章节会指定必须出现在响应文件中的文字。
    如：响应函声明文字、报价有效期承诺等。
    """
    fixed_texts = []
    section_title = getattr(section, "title", "") or ""

    for block in getattr(section, "content", []):
        text = getattr(block, "text", "") or ""
        text = text.strip()
        # 识别固定文本：较长的段落（>30字），不含表格
        if len(text) >= 30 and getattr(block, "type", "") != "table":
            fixed_texts.append({
                "section_ref": section_title,
                "text": text[:500],
                "position": "start",
            })

    return fixed_texts



def _build_per_cell(headers, rows, merge_cells, column_widths):
    """将表格数据转换为 per-cell 格式（用于前端渲染）。
    
    失败时返回 None，避免调用方将空 dict {} 误判为有效 per_cell。
    """
    try:
        from app.infrastructure.table_codec import to_per_cell, to_dict
        td = to_per_cell(headers, rows, merge_cells, column_widths)
        if not td.rows:
            return None
        return to_dict(td)
    except Exception as exc:
        logger.warning("[phase1.5] _build_per_cell 失败: %s", exc)
        return None


def _clean_section_title(title: str) -> str:
    """清洗章节标题用于精确 key 匹配。

    移除序号前缀（"一、""1.""（一）""第一章 "等），全角转半角，压缩空格。
    """
    if not title:
        return ""
    # 全角转半角
    t = title.replace("\u3000", " ").replace("\uff01", "!").replace("\uff0c", ",").replace("\uff1a", ":")
    # 去掉序号前缀：一、二、三...  1. 2.  （一）（二）  第一章 第二章
    t = re.sub(r'^[\u4e00-\u9fff]{1,3}[\u3001\u3002]\s*', '', t)
    t = re.sub(r'^\d+[.、]\s*', '', t)
    t = re.sub(r'^[（(][\u4e00-\u9fff\d]+[）)]\s*', '', t)
    t = re.sub(r'^第[\u4e00-\u9fff\d]+[章章节条]\s*', '', t)
    # 压缩空格
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def extract_format_requirements(sections) -> Optional[Dict]:
    """从文档章节树中提取格式要求。

    Args:
        sections: 文档章节树（list of Section）

    Returns:
        dict or None: {
            "chapter_title": "第三章 比选申请文件格式",
            "required_sections": [...],
            "template_tables": [...],
            "fixed_texts": [...],
            "confidence": 0.85,
        }
    """
    chapter = _find_format_chapter(sections)
    if not chapter:
        logger.info("[phase1.5] 未找到格式要求章节")
        return None

    chapter_title = getattr(chapter, "title", "") or ""

    required_sections = _extract_required_sections(chapter)
    if not required_sections:
        logger.info("[phase1.5] 格式章节 '%s' 无子章节", chapter_title)
        return None

    # 收集固定文本
    fixed_texts = _extract_fixed_texts(chapter)

    # 置信度计算
    confidence = 0.5
    if len(required_sections) >= 3:
        confidence = 0.7 + min(len(required_sections) / 20, 0.2)
    if any(rs["has_template"] for rs in required_sections):
        confidence = min(confidence + 0.1, 0.95)
    # 构建 section_lookup：清洗后的标题 → section dict，用于生成阶段精确匹配
    # 递归展开所有层级的子章节，确保每个章节都能通过 section_lookup 精确命中
    section_lookup = {}
    def _build_lookup_recursive(sec_list):
        for rs in sec_list:
            key = _clean_section_title(rs.get("title", ""))
            if key:
                section_lookup[key] = rs
            children = rs.get("children", [])
            if children:
                _build_lookup_recursive(children)
    _build_lookup_recursive(required_sections)


    # 统计各章节 template_tables 中的表格总数
    _total_tables = sum(len(rs.get("template_tables", [])) for rs in required_sections)
    logger.info(
        "[phase1.5] 格式要求提取完成: chapter='%s', sections=%d, tables=%d, fixed=%d, confidence=%.2f",
        chapter_title, len(required_sections), _total_tables, len(fixed_texts), confidence,
    )

    # 检测封面页
    cover_pages = _detect_cover_sections(sections)

    return {
        "chapter_title": chapter_title,
        "required_sections": required_sections,
        "section_lookup": section_lookup,

        "fixed_texts": fixed_texts,
        "cover_pages": cover_pages,
        "confidence": confidence,
    }
