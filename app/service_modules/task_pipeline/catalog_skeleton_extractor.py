#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
目录骨架提取器 — 从招标文件所有相关章节中提取目录结构。

核心原则：每一份招标文件都生成自己独属的目录。
不再使用硬编码的 10 章骨架，而是从招标文件原文提取真正的结构。

策略（三级递进）：
  第一级（最优先）：从招标文件的"投标文件组成/投标文件的编制"章节提取骨架
    此外还会从"采购需求""技术规格""评审标准"等章节补充更多目录项
  第二级（无显式格式时）：从分析数据推断（由 catalog_inference.py 负责）
  第三级（无任何数据时）：基础兜底（由 catalog_inference.py 负责）

用法:
    from app.service_modules.task_pipeline.catalog_skeleton_extractor import (
        extract_skeleton_from_tender,
        extract_enriched_skeleton_from_tender,
        find_format_section,
    )
    skeleton = extract_enriched_skeleton_from_tender(section_index)
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── 招标文件中描述标书格式的目标章节关键词 ──
TARGET_SECTION_KEYWORDS = frozenset({
    "投标文件组成",
    "投标文件的编制",
    "应提交的文件",
    "投标文件格式",
    "投标文件编写",
    "投标文件的组成",
    "响应文件的组成",
    "投标文件的构成",
    "响应文件构成",
})

# ── 需要跳过的目录段落关键词（TOC 干扰项） ──
SKIP_TITLE_KEYWORDS = frozenset({
    "目录", "contents", "页码",
    "第一章", "第二章", "第三章",
})

# ── 与标书目录无关的章节（不应出现在投标书目录中） ──
SKIP_CHAPTER_KEYWORDS = frozenset({
    "招标公告",
    "投标邀请",
    "投标邀请书",
    "投标人须知",
    "供应商须知",
    "投标须知",
    "投标邀请函",
    "变更公告",
    "更正公告",
    "修改通知",
})

# ── 虽不属于"投标文件组成"但包含投标响应内容的章节 ──
SUPPLEMENTARY_SECTION_KEYWORDS = frozenset({
    "采购需求",
    "技术规格",
    "技术参数",
    "技术标准",
    "技术规范",
    "评审标准",
    "评标标准",
    "评审办法",
    "评标办法",
    "采购内容",
    "供货要求",
    "技术要求",
    "服务要求",
    "项目需求",
    "商务要求",
    "交货要求",
    "验收标准",
})


def find_format_section(section_index: list) -> dict | None:
    """在 section_index 中查找描述"投标文件组成"的目标章节。

    Args:
        section_index: 章节索引列表，每项含 id/title/level/children

    Returns:
        匹配的章节 dict，或 None
    """
    def _search(nodes, depth=0):
        for node in nodes or []:
            title = (node.get("title") or "").strip()
            # 精确匹配
            if title in TARGET_SECTION_KEYWORDS:
                return node
            # 模糊匹配（含关键词且不在 skip 列表中）
            for kw in TARGET_SECTION_KEYWORDS:
                if kw in title:
                    return node
            # 递归搜索子节点
            if "children" in node:
                result = _search(node["children"], depth + 1)
                if result:
                    return result
        return None

    return _search(section_index)


def _is_skip_chapter(title: str) -> bool:
    """判断章节标题是否应被跳过（不属于投标书目录）。"""
    t = title.strip()
    for kw in SKIP_CHAPTER_KEYWORDS:
        if kw in t:
            return True
    return False


def extract_enriched_skeleton_from_tender(
    section_index: list,
    max_depth: int = 2,
) -> list | None:
    """从招标文件所有相关章节中提取目录骨架（增强版）。

    两轮提取：
      第1轮：从"投标文件组成"章节提取主要骨架（与 extract_skeleton_from_tender 相同）
      第2轮：从其他相关章节（采购需求/技术规格/评审标准等）补充目录项

    Args:
        section_index: 章节索引
        max_depth: 最大提取深度

    Returns:
        list[dict] | None: 目录骨架，所有节点保留招标文件的原始表述
    """
    skeleton = []

    # 第1轮：从"投标文件组成"章节提取
    fmt_section = find_format_section(section_index)
    if fmt_section:
        children = fmt_section.get("children", [])
        if children:
            primary = _build_skeleton_from_children(children, depth=0, max_depth=max_depth)
            if primary:
                skeleton.extend(primary)
                logger.info("[skeleton] 第1轮提取: %d 个节点", len(primary))

    # 第2轮：从其他相关章节补充
    supplementary = _extract_supplementary_sections(section_index, fmt_section)
    if supplementary:
        skeleton.extend(supplementary)
        logger.info("[skeleton] 第2轮补充: %d 个节点", len(supplementary))

    if not skeleton:
        logger.info("[skeleton] 增强提取为空，返回 None")
        return None

    logger.info("[skeleton] 增强提取共 %d 个节点", len(skeleton))
    return skeleton


def _extract_supplementary_sections(
    section_index: list,
    exclude_section: dict = None,
) -> list:
    """从 section_index 中提取所有可作为标书目录的章节。

    扫描所有"第X章"章节（或其他格式的主要章节），跳过：
    - 被排除的章节（投标文件组成章节）
    - 应跳过的章节（招标公告/投标人须知等）
    - 纯数字/空白标题

    不再依赖关键词匹配，而是使用 _is_real_chapter 识别真正的章节。

    Returns:
        list[dict]: 补充目录项列表（保留原文表述）
    """
    exclude_id = exclude_section.get("id") if exclude_section else None
    seen = set()
    supplementary = []

    for entry in section_index:
        if entry.get("id") == exclude_id:
            continue
        title = entry.get("title", "").strip()
        if not title:
            continue

        # 用章节识别逻辑代替 level 判断
        from app.service_modules.task_pipeline.analysis_v3.segmented import _is_real_chapter
        if not _is_real_chapter(title):
            continue

        # 去重（防止目录页和正文重复）
        if title in seen:
            continue
        seen.add(title)

        # 跳过无关章节
        if _is_skip_chapter(title):
            continue

        # 保留原文标题
        node = {
            "title": title,
            "source_section_id": entry.get("id"),
            "page_range": entry.get("page_range", []),
            "source": "tender_document",
            "children": [],
        }
        # 提取真正的子标题（一、二、三格式）
        child_ids = entry.get("children_ids", [])
        if child_ids:
            for child_entry in section_index:
                if child_entry.get("id") in child_ids:
                    child_title = child_entry.get("title", "").strip()
                    # 只提取真正的节标题
                    from app.service_modules.task_pipeline.analysis_v3.segmented import _is_real_section
                    if _is_real_section(child_title):
                        cleaned = _clean_title(child_title)
                        if cleaned and not any(kw in cleaned for kw in SKIP_TITLE_KEYWORDS):
                            node["children"].append({
                                "title": cleaned,
                                "source_section_id": child_entry.get("id"),
                                "page_range": child_entry.get("page_range", []),
                                "source": "tender_document",
                                "children": [],
                            })
        supplementary.append(node)

    return supplementary


def _build_skeleton_from_children(
    children: list,
    depth: int = 0,
    max_depth: int = 2,
) -> list:
    """递归构建骨架。

    Args:
        children: 子节点列表
        depth: 当前深度
        max_depth: 最大深度

    Returns:
        list[dict]: 骨架节点列表
    """
    skeleton = []
    for child in children:
        title = _clean_title(child.get("title", ""))
        if not title:
            continue
        # 跳过目录页干扰项
        if any(kw in title for kw in SKIP_TITLE_KEYWORDS):
            continue
        # 跳过纯数字标题（页码）
        if re.match(r'^\d+$', title):
            continue

        node = {
            "title": title,
            "source_section_id": child.get("id"),
            "page_range": child.get("page_range", []),
            "source": "tender_document",
            "children": [],
        }

        # 递归提取子标题
        if depth < max_depth and child.get("children"):
            sub_children = _build_skeleton_from_children(
                child["children"], depth + 1, max_depth
            )
            node["children"] = sub_children

        skeleton.append(node)

    return skeleton


def _clean_title(title: str) -> str:
    """清理标题：去除序号前缀、空白等。"""
    title = (title or "").strip()
    if not title:
        return ""
    # 去除 "一、二、" 等前缀
    title = re.sub(r'^[一二三四五六七八九十零〇百千万亿]+[、，,．.\s]', '', title)
    # 去除 "1. 2." 等前缀
    title = re.sub(r'^\d+[、，,．.\s]', '', title)
    # 去除 "（一）（二）" 等前缀
    title = re.sub(r'^（[一二三四五六七八九十零〇]+）', '', title)
    return title.strip()
