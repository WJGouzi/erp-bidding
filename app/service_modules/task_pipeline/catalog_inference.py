#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
目录推断器 — 从分析数据推断目录结构。

当招标文件没有显式的"投标文件组成"章节时，从 analysis_data 中
按需求类型动态构建目录，替代原来的 10 章硬编码骨架。

策略：
  1. 强制条款（投标函、声明函等）→ 原文顺序插入
  2. 资格要求 → "资格证明文件" 章节
  3. 商务要求 → "商务条款响应" 章节
  4. 技术要求 → "技术方案" 章节（含评分维度展开）
  5. 评分维度 → "评分标准响应" 章节（有评分表时）
  6. 产品清单 → "报价部分" 章节（有产品时）
  7. 服务/售后 → "售后服务" 章节（有服务要求时）
  8. 兜底 → "综合响应" 单章

用法:
    from app.service_modules.task_pipeline.catalog_inference import (
        infer_skeleton_from_analysis,
    )
    skeleton = infer_skeleton_from_analysis(comprehensive_json)
"""

import logging
import re

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  标书 8 章标准骨架（P1 默认优先级）
# ═══════════════════════════════════════════════════════════════

BID_SKELETON = [
    {
        "id": "quotation",
        "title": "报价部分",
        "mandate_level": "HARD",
        "fill_strategy": "TEMPLATE",
        "description": "报价函、报价一览表、分项报价明细表",
        "required": True,
        "detection_key": "quotation",
    },
    {
        "id": "auth_and_declare",
        "title": "法定代表人授权书及声明函",
        "mandate_level": "HARD",
        "fill_strategy": "TEMPLATE",
        "description": "法定代表人授权书、声明函、承诺函等强制格式文件",
        "required": True,
        "detection_key": "mandate_aggregate",
    },
    {
        "id": "qualification",
        "title": "资格证明文件",
        "mandate_level": "SOFT",
        "fill_strategy": "QUALIFICATION",
        "description": "营业执照、资质证书、许可证等资格证明材料",
        "required": True,
        "detection_key": "eligibility",
    },
    {
        "id": "technical",
        "title": "技术方案",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "技术参数响应、产品配置方案",
        "required": True,
        "detection_key": "technical",
    },
    {
        "id": "business",
        "title": "商务条款响应",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "付款方式、交货期、质保等商务条款应答",
        "required": False,
        "detection_key": "business",
    },

    {
        "id": "service",
        "title": "售后服务及培训方案",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "售后服务承诺、技术培训及应急响应方案",
        "required": False,
        "detection_key": "service",
    },
    {
        "id": "other_commitments",
        "title": "其他承诺及补充材料",
        "mandate_level": "FREE",
        "fill_strategy": "MANUAL",
        "description": "其他未归类的承诺函、声明及补充文件",
        "required": False,
        "detection_key": "catch_all",
    },
]


def infer_skeleton_from_analysis(
    comprehensive_json: dict,
    section_index: list = None,
) -> list:
    """从分析数据推断目录结构。

    三级优先级：
      P0: 招标文件显式指定的"响应文件组成" → 严格遵循原文
      P1: 8 章标准投标骨架 + 分析数据动态填充（默认）
      P2: 兜底单章

    Args:
        comprehensive_json: 综合分析 JSON
        section_index: 可选的章节索引

    Returns:
        list[dict]: 目录骨架（至少含一个节点）
    """
    # ── Step 0: P0 检测 ──
    # 检查 comprehensive_json 是否有显式的"响应文件组成"要求
    p0_structure = _detect_p0_response_structure(comprehensive_json, section_index)
    if p0_structure:
        logger.info("[catalog] 使用 P0 招标文件指定目录: %d 章节", len(p0_structure))
        return p0_structure

    # ── Step 1: 从 BID_SKELETON 初始化骨架 ──
    skeleton = _init_skeleton_from_analysis(comprehensive_json, section_index)

    # ── Step 2: 分析数据注入 ──
    _inject_analysis_data(skeleton, comprehensive_json, section_index)

    # ── Step 3: 后处理 ──
    skeleton = _post_process_skeleton(skeleton)

    logger.info("[catalog] 生成目录: %d 章节 (P1)", len(skeleton))
    return skeleton


# ═══════════════════════════════════════════════════════════════
#  P0 检测
# ═══════════════════════════════════════════════════════════════

def _detect_p0_response_structure(comprehensive_json: dict, section_index: list) -> list:
    """检测招标文件是否显式指定了响应文件组成结构。

    如果检测到，返回按原文指定的目录结构；否则返回 None，走 P1。
    """
    # 从 mandate_items 中找是否有"响应文件组成"章节
    mandate_items = comprehensive_json.get("mandate_items", []) or []
    response_structure_keywords = [
        "响应文件组成", "投标文件的组成", "响应文件的组成",
        "比选申请文件的组成", "应答文件的组成",
    ]
    for item in mandate_items:
        title = item.get("title", "") or ""
        for kw in response_structure_keywords:
            if kw in title:
                # 找到了招标文件指定的响应结构
                children = item.get("children", []) or []
                if not children:
                    if section_index and item.get("segment_id"):
                        children = _extract_children_from_section(
                            item["segment_id"], section_index
                        )
                structure = [{
                    "id": f"p0_{i}",
                    "title": c.get("title", f"第{i+1}部分"),
                    "source": "p0_response_structure",
                    "mandate_level": "HARD",
                    "fill_strategy": "TEMPLATE",
                    "priority": "P0",
                    "required": True,
                    "children": [],
                    "availability": {"status": "pending", "confidence": "HIGH"},
                } for i, c in enumerate(children[:10])]
                if structure:
                    return structure
    return None


def _extract_children_from_section(segment_id: str, section_index: list) -> list:
    """从章节索引中提取指定 segment 的子项列表。"""
    if not segment_id or not section_index:
        return []
    for entry in section_index:
        if entry.get("id") == segment_id:
            child_ids = entry.get("children_ids", []) or []
            return [
                {"title": e.get("title", ""), "id": e.get("id", "")}
                for e in section_index if e.get("id") in child_ids
            ]
    return []


# ═══════════════════════════════════════════════════════════════
#  骨架初始化
# ═══════════════════════════════════════════════════════════════

def _init_skeleton_from_analysis(comprehensive_json: dict,
                                  section_index: list) -> list:
    """从 BID_SKELETON 初始化骨架，应用 bid_type 裁剪。"""
    bid_type_raw = comprehensive_json.get("metadata", {}).get("bid_type", "")
    bid_type = bid_type_raw.get("value", "") if isinstance(bid_type_raw, dict) else str(bid_type_raw)

    # 按 bid_type 决定哪些章节 required
    type_map = {
        "SERVICE": ["quotation", "auth_and_declare", "qualification",
                     "technical", "business", "scoring", "service"],
        "GOODS": ["quotation", "auth_and_declare", "qualification",
                   "technical", "business", "scoring", "service"],
        "CONSTRUCTION": ["quotation", "auth_and_declare", "qualification",
                          "technical", "business", "other_commitments"],
        "BID_SELECTION": ["quotation", "auth_and_declare", "qualification",
                           "technical", "business", "scoring",
                           "other_commitments"],
        "SINGLE_SOURCE": ["quotation", "auth_and_declare", "qualification",
                           "business", "other_commitments"],
        "INQUIRY": ["quotation", "auth_and_declare", "qualification",
                     "business", "other_commitments"],
    }
    active_ids = type_map.get(bid_type, [
        "quotation", "auth_and_declare", "qualification",
        "technical", "business", "scoring", "service",
        "other_commitments"
    ])

    return [
        {**s, "required": s["id"] in active_ids,
         "priority": "P1", "availability": {"status": "pending", "confidence": "HIGH"}}
        for s in BID_SKELETON
    ]


# ═══════════════════════════════════════════════════════════════
#  分析数据注入
# ═══════════════════════════════════════════════════════════════

def _inject_analysis_data(skeleton: list, comprehensive_json: dict,
                          section_index: list):
    """将分析数据注入骨架的各章节。"""
    if not skeleton:
        return

    # 提取分析字段
    mandate_items = comprehensive_json.get("mandate_items", []) or []
    hard_items = [m for m in mandate_items if m.get("level") == "HARD"]
    eligibility = comprehensive_json.get("eligibility", {}) or {}
    quals = eligibility.get("qualifications", []) or []
    biz_reqs = comprehensive_json.get("business_requirements", []) or []
    tech_reqs = comprehensive_json.get("technical_requirements", []) or []
    scoring = comprehensive_json.get("scoring", {}) or {}
    dims = scoring.get("dimensions", []) or []
    products = comprehensive_json.get("products", []) or []
    packages = comprehensive_json.get("packages", []) or []

    # ── HARD 项分级 ──
    hard_categories = _categorize_hard_items(hard_items, section_index)

    for chapter in skeleton:
        cid = chapter.get("id")

        if cid == "quotation":
            children = []
            children.append({"title": "报价函", "fill_strategy": "TEMPLATE",
                             "mandate_level": "HARD"})
            children.append({"title": "报价一览表", "fill_strategy": "TEMPLATE",
                             "mandate_level": "HARD"})
            product_count = len(products) if products else _count_core_products(packages)
            if product_count > 0:
                children.append({
                    "title": "分项报价明细表",
                    "fill_strategy": "TEMPLATE",
                    "mandate_level": "HARD",
                    "description": f"含 {product_count} 项产品",
                })
            chapter["children"] = children
            chapter["description"] = f"含 {len(children)} 项报价文件"

        elif cid == "auth_and_declare":
            children = []
            seen_titles = set()
            # 原文顺序添加（skip duplicates）
            for item in hard_categories["aggregated"]:
                t = item.get("title", "")
                if t and t not in seen_titles:
                    seen_titles.add(t)
                    children.append({
                        "title": t,
                        "fill_strategy": "TEMPLATE",
                        "mandate_level": "HARD",
                    })
            # 聚拢其他散落的 HARD 项（上限 5）
            for item in hard_categories["scattered"][:5]:
                t = item.get("title", "")
                if t and t not in seen_titles:
                    seen_titles.add(t)
                    children.append({
                        "title": t,
                        "fill_strategy": "TEMPLATE",
                        "mandate_level": "HARD",
                    })
            if len(hard_categories["scattered"]) > 5:
                logger.warning(
                    "[catalog] 散落HARD项 %d 个，已截断前5个，请检查mandate_classifier质量",
                    len(hard_categories["scattered"])
                )
            # 始终保留的稳定项
            if not any("授权" in c["title"] for c in children):
                children.insert(0, {"title": "法定代表人授权书",
                                     "fill_strategy": "TEMPLATE",
                                     "mandate_level": "HARD"})
            if not any("承诺" in c["title"] for c in children):
                children.append({"title": "承诺函",
                                  "fill_strategy": "TEMPLATE",
                                  "mandate_level": "HARD"})
            chapter["children"] = children
            chapter["description"] = f"共 {len(children)} 项强制格式文件"

        elif cid == "qualification":
            children = _build_qual_children(quals, section_index)
            chapter["children"] = children
            chapter["description"] = f"共 {len(children)} 项资格要求" if children else "待补充资格要求"

        elif cid == "technical":
            children = _build_requirement_children(tech_reqs, "技术")
            if not children:
                children.append({
                    "title": "技术方案响应",
                    "description": "无匹配资料，待补充",
                    "fill_strategy": "MANUAL",
                    "mandate_level": "FREE",
                })
            chapter["children"] = children
            chapter["description"] = f"共 {len(children)} 项技术要求"

        elif cid == "business":
            children = _build_requirement_children(biz_reqs, "商务")
            chapter["children"] = children
            chapter["description"] = f"共 {len(children)} 项商务要求"

        elif cid == "scoring":
            if dims:
                children = [
                    {
                        "title": dim.get("name", f"评分维度 {i+1}"),
                        "description": f"{dim.get('score', 0)} 分",
                        "mandate_level": "FREE",
                        "children": [],
                    }
                    for i, dim in enumerate(dims) if dim.get("name")
                ]
                chapter["children"] = children
                chapter["description"] = (
                    f"共 {len(dims)} 个评分维度，"
                    f"总分 {scoring.get('total_score', 0)} 分"
                )

        elif cid == "service":
            svc_bid_type = comprehensive_json.get("metadata", {}).get("bid_type", "")
            svc_bid_type_val = svc_bid_type.get("value", "") if isinstance(svc_bid_type, dict) else str(svc_bid_type)
            has_service = (
                svc_bid_type_val == "SERVICE"
                or _has_service_content(comprehensive_json)
                or any(sub.get("title", "").find("售后") >= 0
                       for sub in chapter.get("children", []))
            )
            if has_service:
                children = [
                    {"title": "售后服务体系",
                     "description": "售后服务承诺及体系说明",
                     "fill_strategy": "KB_FIRST", "children": []},
                    {"title": "技术培训方案",
                     "description": "产品使用培训计划",
                     "fill_strategy": "KB_FIRST", "children": []},
                ]
                chapter["children"] = children
                chapter["description"] = "售后服务承诺、技术培训及应急响应"
            else:
                # 无服务内容时标记为空
                chapter["children"] = []

        elif cid == "other_commitments":
            # catch_all 聚拢未被归类的 HARD 项
            top_level_hard = hard_categories.get("top_level", [])
            if top_level_hard:
                children = []
                seen = set()
                for item in top_level_hard[:5]:
                    t = item.get("title", "")
                    if t and t not in seen:
                        seen.add(t)
                        children.append({
                            "title": t,
                            "fill_strategy": "TEMPLATE",
                            "mandate_level": "HARD",
                        })
                chapter["children"] = children
                chapter["description"] = f"含 {len(children)} 项补充材料"
            else:
                chapter["children"] = []


# ═══════════════════════════════════════════════════════════════
#  HARD 项三级分级
# ═══════════════════════════════════════════════════════════════

# 格式章节关键词（用于 segment_id 缺失时的文本相似度兜底）
_FORMAT_SECTION_KEYWORDS = [
    "响应文件格式", "投标文件格式", "比选申请文件格式",
    "响应文件的组成", "投标文件的组成",
]
_FORMAT_ITEM_KEYWORDS = [
    "承诺函", "声明函", "授权书", "证明书",
    "报价表", "报价函", "报价一览表",
    "中小企业", "残疾人", "监狱企业",
    "知识产权", "3C", "本国产品",
]


def _categorize_hard_items(hard_items: list, section_index: list) -> dict:
    """将 HARD 项按来源章节层次分成三级。

    Returns:
        {
            "top_level": [...],      # 来自独立顶层章节 → 归入 catch_all
            "aggregated": [...],     # 来自格式章节子项 → 聚合入 auth_and_declare
            "scattered": [...],      # 散落在正文中 → 聚合入 auth_and_declare（上限5）
        }
    """
    groups = {"top_level": [], "aggregated": [], "scattered": []}

    for item in hard_items:
        seg_id = item.get("segment_id")
        title = item.get("title", "")
        category = _determine_hard_category(item, section_index)
        groups.setdefault(category, []).append(item)

    # scattered 超限告警
    if len(groups["scattered"]) > 5:
        logger.warning(
            "[catalog] 散落HARD项 %d 个超过上限5，请检查 mandate_classifier 质量",
            len(groups["scattered"])
        )

    return groups


def _determine_hard_category(item: dict, section_index: list) -> str:
    """单条 HARD 项判定。

    策略:
      1. segment_id 有效 → 查来源章节层次
      2. segment_id 无效 → 文本相似度匹配
      3. 都不可用 → scattered
    """
    seg_id = item.get("segment_id")
    title = item.get("title", "")

    # 策略1: segment_id
    if seg_id and section_index:
        source = _find_section_by_id(seg_id, section_index)
        if source:
            if source.get("level", 1) <= 1:
                # 来源是顶层章节但可能是格式章节本身
                # 如果是"响应文件格式"则降级
                parent_id = source.get("parent_id")
                parent = _find_section_by_id(parent_id, section_index) if parent_id else None
                grandparent_title = (parent or {}).get("title", "")
                if any(kw in grandparent_title for kw in _FORMAT_SECTION_KEYWORDS):
                    return "aggregated"
                return "top_level"
            else:
                # 子章节 → 检查是否在格式章节下
                parent_id = source.get("parent_id")
                current = source
                while parent_id:
                    p = _find_section_by_id(parent_id, section_index)
                    if not p:
                        break
                    if any(kw in p.get("title", "") for kw in _FORMAT_SECTION_KEYWORDS):
                        return "aggregated"
                    parent_id = p.get("parent_id")
                    current = p
                return "aggregated"

    # 策略2: 文本相似度
    if _is_format_item(title):
        return "aggregated"
    if _is_scattered_item(title):
        return "scattered"

    return "scattered"


def _find_section_by_id(seg_id: str, section_index: list) -> dict:
    """从章节索引中查找指定 ID 的条目。"""
    if not seg_id or not section_index:
        return None
    for entry in section_index:
        if entry.get("id") == seg_id:
            return entry
    return None


def _is_format_item(title: str) -> bool:
    """判断是否为格式章节子项。"""
    return any(kw in title for kw in _FORMAT_ITEM_KEYWORDS)


def _is_scattered_item(title: str) -> bool:
    """判断是否为散落的条款项。"""
    # 以编号开头（1. 2. 3. 一、二、）且不含格式关键词 → 散落项
    has_number_prefix = bool(re.match(r'^[\d一二三四五六七八九十]+[、，,．.]', title))
    return has_number_prefix and not _is_format_item(title)


# ═══════════════════════════════════════════════════════════════
#  后处理
# ═══════════════════════════════════════════════════════════════

def _post_process_skeleton(skeleton: list) -> list:
    """目录骨架后处理。

    1. required=False 且无内容 → 移除
    2. required=True 但无内容 → 标记 availability=empty
    3. 否则标记 availability=filled
    """
    result = []
    for chapter in skeleton:
        children = chapter.get("children", []) or []
        has_content = bool(children)

        if not chapter.get("required") and not has_content:
            # 非必需且无内容 → 隐藏
            logger.debug("[catalog] 隐藏非必需章节: %s", chapter.get("title"))
            continue

        # 标记 availability
        if has_content:
            chapter["availability"] = {
                "status": "filled",
                "confidence": "HIGH",
                "warning": "",
            }
        else:
            chapter["availability"] = {
                "status": "empty",
                "confidence": "NA",
                "warning": "无匹配资料，待补充",
            }

        result.append(chapter)

    # 兜底
    if not result:
        result = [{
            "id": "fallback",
            "title": "综合响应",
            "description": "综合响应招标文件要求",
            "source": "fallback",
            "mandate_level": "FREE",
            "fill_strategy": "KB_FIRST",
            "priority": "P2",
            "required": True,
            "availability": {"status": "pending", "confidence": "LOW",
                             "warning": ""},
            "children": [],
        }]

    return result


def _build_qual_children(qualifications: list, section_index: list = None) -> list:
    """构建资格证明文件的子项列表。"""
    children = []
    for i, q in enumerate(qualifications):
        req = q.get("requirement") or q.get("text") or ""
        if not req:
            continue
        children.append({
            "title": req[:60] + ("..." if len(req) > 60 else ""),
            "description": req[:200],
            "source_segment_id": q.get("source_segment_ids", [None])[0]
            if q.get("source_segment_ids") else None,
            "children": [],
        })
    return children[:10]  # 最多10项


def _build_requirement_children(requirements: list, prefix: str) -> list:
    """构建商务/技术要求的子项列表。"""
    children = []
    for i, req in enumerate(requirements):
        if isinstance(req, dict):
            text = req.get("requirement") or req.get("text") or req.get("title") or ""
        else:
            text = str(req)
        if not text:
            continue
        children.append({
            "title": f"{prefix}要求 {i+1}",
            "description": text[:200],
            "children": [],
        })
    return children[:10]


def _count_core_products(packages: list) -> int:
    count = 0
    for pkg in packages:
        params = pkg.get("parameters", {}) or {}
        core = params.get("core_products", []) or []
        count += len(core)
    return count


def _has_service_content(comprehensive_json: dict) -> bool:
    """检查是否有服务类相关内容。"""
    text_fields = [
        str(comprehensive_json.get(k, ""))
        for k in ("service_requirements", "售后", "服务")
        if k in comprehensive_json
    ]
    for text in text_fields:
        if text and len(text) > 50:
            return True
    return False


def _find_page_range(section_index: list, segment_id: str) -> list:
    """从 section_index 中查找 segment_id 对应的页码范围。"""
    for sec in section_index:
        if sec.get("id") == segment_id:
            return sec.get("page_range", [])
    return []
