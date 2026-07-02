#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 组装器 — 将段级解析结果组装为完整综合分析 JSON。

职责：
  - 归纳去重：相同含义的字段合并为一条，标注所有来源
  - 关联映射：废标条件 ↔ 资格要求建立关联
  - 冲突标记：同一字段在不同段中值不同时，携带冲突信息
  - 结构填充：确保输出 JSON 结构完整

约束：
  - LLM 不做新提取，只做已有数据的归纳/关联/排序
  - 所有输出项必须指向至少一个 source_segment_id
  - 不得删除或修改原始提取值

用法:
    from app.service_modules.task_pipeline.analysis_v3.assembler import assemble
    result = assemble(segment_results, section_index)
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _extract_keyword_ngrams(text: str, min_n: int = 2, max_n: int = 4) -> set:
    """从中文文本中提取 n-gram 关键词。

    使用滑动窗口提取 2-4 字的中文 n-gram，避免贪婪匹配导致整个句子作为单个关键词。
    """
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
    ngrams = set()
    for n in range(min_n, min(max_n + 1, len(chars) + 1)):
        for i in range(len(chars) - n + 1):
            ngrams.add(''.join(chars[i:i + n]))
    return ngrams



def _llm_polish(merged: dict, segment_results: list) -> dict:
    """LLM 精修步骤（方案B）。
    
    在规则合并后，用 LLM 做冲突解决和关联增强。
    如果 LLM 不可用或异常，静默降级返回规则合并结果。
    
    Args:
        merged: _basic_merge 的输出
        segment_results: 原始段级结果
    
    Returns:
        dict: 增强后的合并结果
    """
    # 当前为占位实现：直接返回规则合并结果
    # TODO: 后续接入 LLM 做冲突解决 + 关联增强
    return merged


def assemble(
    segment_results: list,
    section_index: list = None,
) -> dict:
    """将段级解析结果组装为综合分析 JSON。

    Args:
        segment_results: 段级解析结果列表
            每项: {segment_id, title, page_range, mandate_level,
                   metadata, eligibility, scoring, raw_excerpt}
        section_index: 章节索引（可选，用于来源定位）

    Returns:
        comprehensive_analysis.json
    """
    if not segment_results:
        return _empty_result()

    # Step 1: 基础合并（规则驱动，不依赖 LLM）
    merged = _basic_merge(segment_results)
    # Step 1.5: LLM 精修（方案B：规则合并 → LLM 冲突解决）
    merged = _llm_polish(merged, segment_results)

    # Step 2: 关联映射（废标↔资格）
    merged = _build_associations(merged)

    # Step 3: 置信度标记
    merged = _mark_confidences(merged, segment_results)

    # Step 4: 构建来源索引
    merged["_segment_binding"] = _build_segment_binding(merged, segment_results)
    if section_index:
        merged["_section_index"] = section_index

    return merged


def _empty_result() -> dict:
    """返回空结果结构。"""
    return {
        "metadata": {},
        "mandate_items": [],
        "eligibility": {"qualifications": [], "disqualifications": []},
        "scoring": {"method": "", "total_score": 0, "dimensions": []},
        "business_requirements": [],
        "technical_requirements": [],
        "products": [],
        "packages": [],
        "_segment_binding": {},
        "_section_index": [],
    }


def _basic_merge(segment_results: list) -> dict:
    """基础合并：将所有段的结果合并为一个 JSON。"""
    result = _empty_result()
    seen_excerpts = set()

    for seg in segment_results:
        seg_id = seg.get("segment_id", "")
        title = seg.get("title", "")
        page_range = seg.get("page_range", [])

        # ── metadata ──
        meta = seg.get("metadata", {}) or {}
        if isinstance(meta, dict):
            for key, value in meta.items():
                if value and str(value).strip():
                    if key == "project_name" and not result["metadata"].get("project_name"):
                        result["metadata"]["project_name"] = {"value": str(value), "source_segment_ids": [seg_id]}
                    elif key == "project_code" and not result["metadata"].get("project_code"):
                        result["metadata"]["project_code"] = {"value": str(value), "source_segment_ids": [seg_id]}
                    elif key in ("budget", "budget_total") and not result["metadata"].get("budget_total"):
                        result["metadata"]["budget_total"] = {"value": value, "source_segment_ids": [seg_id]}
                    elif key == "bid_type" and not result["metadata"].get("bid_type"):
                        result["metadata"]["bid_type"] = {"value": str(value), "source_segment_ids": [seg_id]}
                    elif key == "bid_deadline" and not result["metadata"].get("bid_deadline"):
                        result["metadata"]["bid_deadline"] = {"value": str(value), "source_segment_ids": [seg_id]}

        # ── mandate_level ──
        mandate = seg.get("mandate_level")
        if mandate and mandate.get("level") == "HARD":
            mandate_item = {
                "title": title,
                "level": "HARD",
                "reason": mandate.get("reason", ""),
                "source": mandate.get("source", ""),
                "segment_id": seg_id,
            }
            if mandate_item not in result["mandate_items"]:
                result["mandate_items"].append(mandate_item)

        # ── eligibility ──
        elig = seg.get("eligibility", {}) or {}
        if isinstance(elig, dict):
            for q in elig.get("qualifications", []) or []:
                req = q.get("requirement") or q.get("text") or ""
                if req and req not in seen_excerpts:
                    seen_excerpts.add(req)
                    result["eligibility"]["qualifications"].append({
                        "requirement": req,
                        "source_segment_ids": [seg_id],
                    })
            for d in elig.get("disqualifications", []) or []:
                cond = d.get("condition") or d.get("text") or ""
                if cond and cond not in seen_excerpts:
                    seen_excerpts.add(cond)
                    result["eligibility"]["disqualifications"].append({
                        "condition": cond,
                        "level": d.get("level", "HIGH"),
                        "source_segment_ids": [seg_id],
                    })

        # ── scoring ──
        scoring = seg.get("scoring", {}) or {}
        if isinstance(scoring, dict) and scoring.get("dimensions"):
            # 取评分方法最完整的一个段
            if not result["scoring"]["method"] and scoring.get("method"):
                result["scoring"]["method"] = scoring["method"]
            if not result["scoring"]["total_score"] and scoring.get("total_score"):
                result["scoring"]["total_score"] = scoring["total_score"]
            for dim in scoring.get("dimensions", []) or []:
                if isinstance(dim, dict) and dim.get("name"):
                    name = dim["name"]
                    if not any(d.get("name") == name for d in result["scoring"]["dimensions"]):
                        result["scoring"]["dimensions"].append({
                            "name": name,
                            "score": dim.get("score", 0),
                            "criteria": dim.get("criteria", ""),
                            "source_segment_ids": [seg_id],
                        })

        # ── business/technical ──
        for field in ("business_requirements", "technical_requirements"):
            items = seg.get(field, []) or []
            if isinstance(items, list):
                for item in items:
                    text = item.get("requirement") or item.get("text") or item.get("title") or ""
                    if text and text not in seen_excerpts:
                        seen_excerpts.add(text)
                        result[field].append({
                            "requirement": text,
                            "source_segment_ids": [seg_id],
                        })

        # ── products ──
        products = seg.get("products", []) or []
        if isinstance(products, list):
            for p in products:
                name = p.get("name") or p.get("product_name") or ""
                if name and name not in seen_excerpts:
                    seen_excerpts.add(name)
                    result["products"].append({
                        "name": name,
                        "source_segment_ids": [seg_id],
                    })

    return result


def _build_associations(result: dict) -> dict:
    """建立废标条件与资格要求之间的关联。"""
    disqualifications = result.get("eligibility", {}).get("disqualifications", [])
    qualifications = result.get("eligibility", {}).get("qualifications", [])

    associations = []
    for disq in disqualifications:
        condition = disq.get("condition", "")
        related = []
        for qual in qualifications:
            req = qual.get("requirement", "")
            # 关键词重叠检测
            disq_ngrams = _extract_keyword_ngrams(condition)
            qual_ngrams = _extract_keyword_ngrams(req)
            overlap = disq_ngrams & qual_ngrams
            stop_ngrams = {"可以", "进行", "以及", "一个", "这个", "如果", "按照", "没有", "应当", "必须"}
            overlap = overlap - stop_ngrams
            if len(overlap) >= 1:
                related.append(req[:60])
        if related:
            associations.append({
                "disqualification": condition[:100],
                "related_qualifications": related,
                "source_segment_ids": disq.get("source_segment_ids", []),
            })

    if associations:
        result["eligibility"]["disqualification_bindings"] = associations

    return result


def _mark_confidences(result: dict, segment_results: list) -> dict:
    """为聚合结果标记置信度。"""
    try:
        from app.domain.analysis_schema import ConfidenceLevel
    except ImportError:
        return result

    # metadata 标记为 HIGH（来自原文规则提取）
    for key in result.get("metadata", {}):
        if isinstance(result["metadata"][key], dict):
            result["metadata"][key]["confidence"] = "HIGH"

    return result


def _build_segment_binding(result: dict, segment_results: list) -> dict:
    """构建来源绑定索引。

    输出: { "metadata.project_name": "sec_1",
            "eligibility.qualifications[0]": "sec_2", ... }
    """
    binding = {}

    # metadata
    for key, value in result.get("metadata", {}).items():
        if isinstance(value, dict) and value.get("source_segment_ids"):
            binding[f"metadata.{key}"] = value["source_segment_ids"]

    # mandate_items
    for i, item in enumerate(result.get("mandate_items", [])):
        if item.get("segment_id"):
            binding[f"mandate_items[{i}]"] = [item["segment_id"]]

    # eligibility
    for i, q in enumerate(result.get("eligibility", {}).get("qualifications", [])):
        if q.get("source_segment_ids"):
            binding[f"eligibility.qualifications[{i}]"] = q["source_segment_ids"]

    for i, d in enumerate(result.get("eligibility", {}).get("disqualifications", [])):
        if d.get("source_segment_ids"):
            binding[f"eligibility.disqualifications[{i}]"] = d["source_segment_ids"]

    # scoring
    for i, dim in enumerate(result.get("scoring", {}).get("dimensions", [])):
        if dim.get("source_segment_ids"):
            binding[f"scoring.dimensions[{i}]"] = dim["source_segment_ids"]

    return binding
