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




# ═══════════════════════════════════════════════════════════════
#  封面检测与合并逻辑
# ═══════════════════════════════════════════════════════════════

# 说明性关键词 — 标题含"封面"但内容为"应包含…"等说明句式时，不标封面
_COVER_EXPLANATORY_KEYWORDS = [
    "应包含", "须体现", "应当", "应包括", "必须", "需包含", "要求", "说明",
    "需要", "建议", "请将", "请使用",
]
# 占位符模式
_PLACEHOLDER_PATTERNS = re.compile(r'_+|XXX+|xx+')
# 标签式结束符模式
_LABEL_END_PATTERNS = re.compile(r'[：:：]$')
# 疑似封面的特征段落（无编号+短文本+居中）
_COVER_BODY_KEYWORDS = re.compile(
    r'投标[文件函]|响应文件|资格性|资\s*格\s*性|其\s*他\s*响\s*应|采购项目|项目名称|'
    r'投标单位|法定代表|投标日期|文件编号|采购编号'
)


def _is_template_style_content(template_content: List[Dict]) -> bool:
    """判断 template_content 是否是封面模板样式（而非说明性内容）。

    封面模板特征：
    - 内容块少（通常 ≤20 块）
    - 占位符密度高（___ / XXX）
    - 标签式短句多（"项目名称："、"编号："）
    - 无说明性句式（"应包含"、"须体现"）
    - 没有长段落（每条 < 100 字）
    - 无复杂层级

    说明性内容特征：
    - 含"应包含"、"须体现"、"应当"等祈使关键词
    - 整段长文字（≥ 100 字）
    - 无占位符
    - 有条理的多段说明
    """
    if not template_content:
        return False

    total_text = ""
    placeholder_count = 0
    label_count = 0
    long_paragraph_count = 0
    explanatory_count = 0

    for block in template_content:
        if block.get("type") != "text":
            continue
        text = block.get("text", "") or ""
        total_text += text

        # 检查占位符
        if _PLACEHOLDER_PATTERNS.search(text):
            placeholder_count += 1

        # 检查标签式结束（"项目名称："、"编号："）
        if _LABEL_END_PATTERNS.search(text) and len(text) <= 30:
            label_count += 1

        # 检查说明性关键词
        for kw in _COVER_EXPLANATORY_KEYWORDS:
            if kw in text:
                explanatory_count += 1
                break

        # 长段落
        if len(text) >= 100:
            long_paragraph_count += 1

    # 全是空或极少内容
    if not total_text.strip():
        return False

    # 说明性 → 不是封面
    if explanatory_count > 0 and label_count == 0:
        return False

    # 无标签且多为长段落 → 说明性
    if label_count == 0 and long_paragraph_count >= 2:
        return False

    # 有标签或有占位符 → 模板式
    if label_count > 0 or placeholder_count > 0:
        return True

    # 混合情况：短内容（≤200字）+ 无说明关键词 → 可能是简单封面
    if len(total_text.strip()) <= 200 and explanatory_count == 0:
        return True

    return False


def _get_content_block_font_info(section_obj) -> List[Dict]:
    """从原始 Section 的 ContentBlock 中提取 font 信息。
    
    返回与 template_content 平行的 font 信息列表（按 content 顺序）。
    """
    font_info_list = []
    for block in getattr(section_obj, "content", []):
        info = {}
        try:
            fn = getattr(block, "font_name", "") or ""
            if fn:
                info["font_name"] = fn
            fs = getattr(block, "font_size", None)
            if fs is not None:
                info["font_size"] = fs
            bd = getattr(block, "bold", False)
            if bd:
                info["bold"] = True
            al = getattr(block, "alignment", None)
            if al:
                info["alignment"] = al
        except Exception:
            pass
        font_info_list.append(info)
    return font_info_list


def _inject_font_into_template_content(template_content: List[Dict],
                                        font_info_list: List[Dict]) -> None:
    """将 font 信息注入 template_content 的每个块（覆盖检测阶段用）。"""
    for tb, fi in zip(template_content, font_info_list):
        if fi and tb.get("type") == "text":
            tb["font"] = fi


def _detect_placeholder(template_content: List[Dict]) -> None:
    """检测 template_content 中的占位符并标记。
    
    占位符模式：
    - 纯下划线: "____" / "___________" 等（长度≥2）
    - XXX 占位符: "XXX" / "XXX（单位名称）" 
    - 空字符串: ""（如果紧跟在标签后面）
    - 混合型: "投标单位（盖章）：XXX" 中的 XXX 部分
    """
    for block in template_content:
        if block.get("type") != "text":
            continue
        text = block.get("text", "") or ""
        if not text.strip():
            # 空字符串 → 设为 placeholder
            block["placeholder"] = True
            continue
        # 纯下划线占位符
        if re.fullmatch(r'_{2,}', text.strip()):
            block["placeholder"] = True
            block["fill_mode"] = "replace"
            continue
        # 纯 XXX
        if re.fullmatch(r'[xX]{2,}', text.strip()):
            block["placeholder"] = True
            block["fill_mode"] = "replace"
            continue
        # 混合型: 文本中含 XXX 或下划线，需要部分替换
        if _PLACEHOLDER_PATTERNS.search(text):
            block["placeholder"] = True
            block["fill_mode"] = "partial"
            continue
        # 默认：不是占位符
        block["placeholder"] = False


def _is_same_page(sec_a_raw, sec_b_raw) -> bool:
    """判断两个原始 Section 是否在同一页。"""
    pr_a = getattr(sec_a_raw, "page_range", []) or []
    pr_b = getattr(sec_b_raw, "page_range", []) or []
    if not pr_a or not pr_b:
        # 无 page_range 时，保守假设为同页（仅靠相邻关系）
        return True
    # 有重叠即视为同页
    a_start, a_end = pr_a[0], pr_a[-1] if len(pr_a) >= 2 else pr_a[0]
    b_start, b_end = pr_b[0], pr_b[-1] if len(pr_b) >= 2 else pr_b[0]
    return not (a_end < b_start or b_end < a_start)


def _merge_cover_sections(required: List[Dict],
                           raw_section_map: Dict[str, object]) -> List[Dict]:
    """封面检测与合并后处理。

    遍历 required_sections 列表，检测封面指示器/封面主体，
    执行合并和标记，同时从原始 Section 注入 font 信息。
    """
    if not required:
        return required

    result = []
    skip_until = -1

    for i, sec in enumerate(required):
        if i < skip_until:
            continue

        title = sec.get("title", "") or ""
        has_cover_kw = "封面" in title or "封皮" in title
        tc = sec.get("template_content", []) or []

        if not has_cover_kw:
            result.append(sec)
            continue

        # ===== 标题含"封面/封皮" → 进入判定 =====
        if tc:
            # 有内容 → 判定是封面模板还是封面说明
            if _is_template_style_content(tc):
                # 情形 B: 封面主体（标题含封面 + 内容为模板）
                sec["is_cover"] = True
                _detect_placeholder(tc)
                # 从原始 section 注入 font
                raw = raw_section_map.get(title)
                if raw:
                    fi = _get_content_block_font_info(raw)
                    _inject_font_into_template_content(tc, fi)
                result.append(sec)
            else:
                # 情形: 封面说明（"封面应包含…"）→ 不标封面
                sec.pop("is_cover", None)
                result.append(sec)
        else:
            # 情形 A: 内容为空 → 封面指示器
            # 看下一个 section
            merged = False
            if i + 1 < len(required):
                next_sec = required[i + 1]
                next_title = next_sec.get("title", "") or ""
                next_tc = next_sec.get("template_content", []) or []
                next_raw = raw_section_map.get(next_title)

                # 检查同页
                raw_cur = raw_section_map.get(title)
                if raw_cur and next_raw:
                    same_page = _is_same_page(raw_cur, next_raw)
                else:
                    same_page = True  # 无 page_range 时保守假设同页

                if same_page and _is_template_style_content(next_tc):
                    # 合并: 用下个 section 的内容，标记 is_cover
                    merged_sec = dict(next_sec)
                    merged_sec["is_cover"] = True
                    merged_sec["order"] = sec.get("order")
                    # 注入 font
                    if next_raw:
                        fi = _get_content_block_font_info(next_raw)
                        _inject_font_into_template_content(
                            merged_sec.get("template_content", []), fi)
                    _detect_placeholder(merged_sec.get("template_content", []))
                    result.append(merged_sec)
                    skip_until = i + 2
                    merged = True

            if not merged:
                result.append(sec)

    return result
def extract_format_requirements(sections) -> Optional[Dict]:
    """从文档章节树中提取格式要求。

    Args:
        sections: 文档章节树（list of Section）

    Returns:
        dict or None: {
            "chapter_title": "第三章 比选申请文件格式",
            "required_sections": [...],

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

    # ===== 封面检测与合并（在 required_sections 上后处理） =====
    # 构建原始 Section 的标题映射（用于 page_range 和 font 信息）
    raw_section_map = {}
    for _child in getattr(chapter, "children", []):
        _ct = getattr(_child, "title", "") or ""
        if _ct:
            raw_section_map[_ct] = _child
        # 子章节也加入映射
        for _sub in getattr(_child, "children", []):
            _st = getattr(_sub, "title", "") or ""
            if _st and _st not in raw_section_map:
                raw_section_map[_st] = _sub

    required_sections = _merge_cover_sections(required_sections, raw_section_map)

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


    _total_tables = 0
    logger.info(
        "[phase1.5] 格式要求提取完成: chapter='%s', sections=%d, tables=%d, fixed=%d, confidence=%.2f",
        chapter_title, len(required_sections), _total_tables, len(fixed_texts), confidence,
    )

    return {
        "chapter_title": chapter_title,
        "required_sections": required_sections,
        "section_lookup": section_lookup,

        "fixed_texts": fixed_texts,
        "confidence": confidence,
    }
