#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分段分析引擎 — 按二级标题将招标文件分段后独立分析。

核心流程：
  1. 从 section_index 提取 level-2 分段（若无则降级为 level-1）
  2. 对每个分段运行 mandate_classifier + 资格扫描 + 评分提取
  3. 返回 segment_results 列表

用法:
    from app.service_modules.task_pipeline.analysis_v3.segmented import (
        run_segmented_analysis,
    )
    segment_results = run_segmented_analysis(doc, section_index, full_text)
"""

import logging
import re

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  索引工具
# ═══════════════════════════════════════════════════════════════

def _build_id_map(section_index):
    """构建 section_id → entry 的映射。"""
    return {e["id"]: e for e in section_index}


def _build_parent_children_map(section_index):
    """构建 parent_id → [child_entries] 的映射。"""
    mapping = {}
    for e in section_index:
        pid = e.get("parent_id")
        if pid:
            mapping.setdefault(pid, []).append(e)
    return mapping


# ═══════════════════════════════════════════════════════════════
#  分段提取
# ═══════════════════════════════════════════════════════════════

# ── 真正的中文章节编号模式 ──
_CN_CHAPTER_RE = re.compile(r'^第[一二三四五六七八九十零〇百千万亿]+[章节篇]')
_CN_SECTION_RE = re.compile(r'^[一二三四五六七八九十零〇百千万亿]+[、，]')


def _is_real_chapter(title):
    """判断标题是否为真正的章节目录（如：第一章、第二章）。"""
    return bool(_CN_CHAPTER_RE.match(title.strip()))


def _is_real_section(title):
    """判断标题是否为真正的节标题（如：一、采购项目基本情况）。"""
    return bool(_CN_SECTION_RE.match(title.strip()) or _CN_CHAPTER_RE.match(title.strip()))


def get_level2_segments(section_index):
    """获取章节级分段单位。

    策略：
      1. 找到所有真正的章节（第X章），去重
      2. 每个章节作为一个分段
      3. 若找不到真正章节，降级为 level-1 节点
      4. 若仍找不到，使用全部索引

    每个 segment 的文本包含该章节及其所有子节点的内容。
    """
    # 策略1：找真正的章节（第X章），保留最完整版本
    chapter_groups = {}
    for e in section_index:
        title = e.get("title", "").strip()
        if not _is_real_chapter(title):
            continue
        # 用章节号去重（"第三章"），但保留有子节点的版本
        ch_match = re.match(r'(第[一二三四五六七八九十]+[章节篇])', title)
        ch_key = ch_match.group(1) if ch_match else title
        existing = chapter_groups.get(ch_key)
        if existing is None:
            chapter_groups[ch_key] = e
        else:
            # 优先保留有子节点的、子节点更多的版本
            existing_children = len(existing.get("children_ids", []))
            new_children = len(e.get("children_ids", []))
            if new_children > existing_children:
                # 额外检查：新版本如果出现在后面，更可能是正文而非目录引用
                chapter_groups[ch_key] = e
            elif new_children == existing_children:
                # 子节点数相同时，保留靠后的版本（更可能是正文）
                chapter_groups[ch_key] = e
    
    chapters = list(chapter_groups.values())
    chapters.sort(key=lambda e: e.get("id", ""))  # 保持文档顺序
    
    if chapters:
        return chapters
    
    # 策略2：降级为 level=1 的节点
    level1 = [e for e in section_index if e.get("level") == 1]
    if level1:
        return level1
    
    # 策略3：全部
    return section_index


def _find_root_section(doc, sec_id, section_index):
    """在 doc.sections 树中找到 sec_id 对应的 Section 对象。

    通过标题 + 层级匹配。
    如果找到的节点无内容且无子节点（TOC 占位），
    继续搜索同标题/层级的后续节点（正文版本）。
    """
    target = _build_id_map(section_index).get(sec_id)
    if not target:
        return None

    title = target.get("title", "")
    level = target.get("level", 1)

    candidates = []

    def _walk(sections):
        for sec in sections:
            if (sec.title or "").strip() == title.strip() and sec.level == level:
                candidates.append(sec)
            _walk(sec.children)

    _walk(doc.sections)

    if not candidates:
        return None

    # 优先返回有子节点或直接内容的版本
    for c in candidates:
        if c.children or any(b.text for b in c.content):
            return c
    # 都无内容时返回第一个
    return candidates[0]


def _get_segment_text(doc, sec_id, section_index):
    """获取 segment 的完整文本（自身 + 子章节）。"""
    root = _find_root_section(doc, sec_id, section_index)
    if not root:
        return ""
    # 使用已有的 _section_to_text（在 __init__.py 中定义为模块级函数）
    # 通过延迟导入避免循环引用
    from . import _section_to_text as _to_text
    return _to_text(root)


# ═══════════════════════════════════════════════════════════════
#  单段分析
# ═══════════════════════════════════════════════════════════════

def _analyze_eligibility_for_segment(seg_sections):
    """对 segment 的 sections 运行资格扫描。"""
    result = {"qualifications": [], "disqualifications": [], "starred_requirements": []}
    if not seg_sections:
        return result
    try:
        from .phase2_extractor import scan_eligibility_v2 as scan_v2
        full = scan_v2(seg_sections)
        if full:
            result["qualifications"] = full.get("qualifications", [])
            result["disqualifications"] = full.get("disqualifications", [])
            result["starred_requirements"] = full.get("starred_requirements", [])
    except Exception as exc:
        logger.warning("[segmented] 资格扫描异常: %s", exc)
    return result


def _analyze_scoring_for_segment(seg_text):
    """对 segment 文本运行评分提取。"""
    result = {"method": "", "total_score": 0, "dimensions": []}
    if not seg_text:
        return result
    try:
        from .phase3_scoring import _detect_text_tables, parse_scoring_table
        text_tables = _detect_text_tables(seg_text)
        if text_tables:
            for table in text_tables:
                parsed = parse_scoring_table(table)
                if parsed and parsed.get("dimensions"):
                    result["dimensions"] = parsed["dimensions"]
                    result["method"] = parsed.get("method", "综合评分法")
                    break
    except Exception as exc:
        logger.warning("[segmented] 评分提取异常: %s", exc)
    return result


def _analyze_mandate_for_segment(title, text):
    """对 segment 运行强制条款分类。"""
    default = {"level": "FREE", "reason": "未检测", "source": ""}
    try:
        from ....infrastructure.mandate_classifier import classify_mandate
        m = classify_mandate(title, text[:500], [], [])
        return {
            "level": m.get("level", "FREE"),
            "reason": m.get("reason", ""),
            "source": m.get("source", ""),
        }
    except Exception:
        return default


def analyze_single_segment(doc, seg_node, section_index, full_text):
    """对单个 segment 运行独立分析。

    Returns:
        dict: {segment_id, title, page_range, mandate_level,
               metadata, eligibility, scoring, raw_excerpt}
    """
    seg_id = seg_node["id"]
    seg_title = seg_node.get("title", "")
    page_range = seg_node.get("page_range", [])

    # 获取文本
    seg_text = _get_segment_text(doc, seg_id, section_index)

    # 获取对应 sections（用于资格扫描）
    root_sec = _find_root_section(doc, seg_id, section_index)
    seg_sections = [root_sec] if root_sec else []

    # 运行分析
    mandate = _analyze_mandate_for_segment(seg_title, seg_text)
    eligibility = _analyze_eligibility_for_segment(seg_sections)
    scoring = _analyze_scoring_for_segment(seg_text)

    return {
        "segment_id": seg_id,
        "title": seg_title,
        "page_range": page_range,
        "mandate_level": mandate,
        "metadata": {},
        "eligibility": eligibility,
        "scoring": scoring,
        "raw_excerpt": seg_text[:500],
    }


def run_segmented_analysis(doc, section_index, full_text):
    """运行全部分段分析，生成 segment_results。

    Args:
        doc: StructuredDocument
        section_index: list[dict] — 章节索引
        full_text: str — 完整原文

    Returns:
        list[dict]: segment_results，每项：
            {segment_id, title, page_range, mandate_level,
             metadata, eligibility, scoring, raw_excerpt}
    """
    segments = get_level2_segments(section_index)
    if not segments:
        logger.info("[segmented] 无可用分段，跳过")
        return []

    # 排除合同模板章节（不分析、不生成标书）
    _CONTRACT_KW = ["合同模板", "合同条款", "合同样本", "合同范本"]
    filtered_segments = [
        s for s in segments
        if not any(kw in (s.get("title", "") or "") for kw in _CONTRACT_KW)
    ]
    skipped = len(segments) - len(filtered_segments)
    if skipped:
        logger.info("[segmented] 跳过 %d 个合同模板章节", skipped)

    logger.info("[segmented] 开始分段分析: %d segments", len(filtered_segments))
    results = []
    for seg in filtered_segments:
        try:
            result = analyze_single_segment(doc, seg, section_index, full_text)
            results.append(result)
        except Exception as exc:
            logger.warning("[segmented] segment %s 分析异常: %s",
                           seg.get("id"), exc)

    logger.info("[segmented] 分段分析完成: %d/%d",
                len(results), len(segments))
    return results
