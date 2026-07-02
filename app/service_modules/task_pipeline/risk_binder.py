#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
废标条件 → 生成约束转换器。

核心原则：废标条件不写进标书，只作为生成行为的约束。

转换映射：
  ★号参数 → MANDATORY_MATCH (必须逐项响应)
  盖章缺失 → REQUIRED_SIGNATURE (必须确认已盖或标注待盖)
  资质不符 → BIND_TO_SUBJECT (必须绑定主体资料)
  业绩不足 → EVIDENCE_REQUIRED (无证据则留白)
  格式错误 → FORMAT_LOCK (必须原文复制，不可改写)

用法:
    from app.service_modules.task_pipeline.risk_binder import (
        convert_to_guardrails,
        bind_guardrails_to_outline,
    )
    guardrails = convert_to_guardrails(disqualifications, qualifications)
    outline = bind_guardrails_to_outline(outline, guardrails)
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── 约束类型 ──
GUARDRAIL_TYPES = {
    "MANDATORY_MATCH": "★项必须逐项响应，不得遗漏或改写",
    "EVIDENCE_REQUIRED": "无匹配证据时须留白，不得编造",
    "REQUIRED_SIGNATURE": "盖章位须确认已盖或标注待盖章",
    "BIND_TO_SUBJECT": "必须绑定主体资料，无匹配则留白",
    "FORMAT_LOCK": "原文锁定，不可改写",
}

# 关键词 → guardrail 映射
_KEYWORD_MAP = [
    (re.compile(r'[★☆※]'), "MANDATORY_MATCH"),
    (re.compile(r'盖章|签章|公章|签字|签名'), "REQUIRED_SIGNATURE"),
    (re.compile(r'原件'), "REQUIRED_SIGNATURE"),
    (re.compile(r'资质|资格|许可|证书|备案'), "BIND_TO_SUBJECT"),
    (re.compile(r'业绩|案例|合同'), "EVIDENCE_REQUIRED"),
    (re.compile(r'格式'), "FORMAT_LOCK"),
    (re.compile(r'声明|承诺|函$'), "FORMAT_LOCK"),
]


def convert_to_guardrails(
    disqualifications: list,
    qualifications: list = None,
) -> list:
    """将废标条件和资格要求转换为生成约束。

    Args:
        disqualifications: 废标条件列表，每项含 condition/level/source
        qualifications: 资格要求列表，每项含 id/requirement

    Returns:
        list[dict]: 生成约束列表
    """
    guardrails = []
    seen_conditions = set()

    for disq in disqualifications or []:
        condition = (disq.get("condition") or disq.get("text") or "").strip()
        if not condition or condition in seen_conditions:
            continue
        seen_conditions.add(condition)

        guardrail = _match_guardrail(condition)
        guardrail["source"] = f"disqualification:{condition[:100]}"
        guardrail["detail"] = condition[:300]
        guardrails.append(guardrail)

    # 资格要求中的★项也转为 MANDATORY_MATCH
    for qual in qualifications or []:
        requirement = (qual.get("requirement") or qual.get("text") or "").strip()
        if not requirement:
            continue
        if '★' in requirement or '※' in requirement:
            guardrails.append({
                "type": "MANDATORY_MATCH",
                "action": "VERIFY_OR_LEAVE_BLANK",
                "source": f"qualification:{requirement[:100]}",
                "detail": requirement[:300],
            })

    return guardrails


def _match_guardrail(condition: str) -> dict:
    """根据条件文本匹配对应的 guardrail 类型。"""
    for pattern, gtype in _KEYWORD_MAP:
        if pattern.search(condition):
            action_map = {
                "MANDATORY_MATCH": "VERIFY_OR_LEAVE_BLANK",
                "EVIDENCE_REQUIRED": "SKIP_IF_NO_EVIDENCE",
                "REQUIRED_SIGNATURE": "MARK_AS_PENDING",
                "BIND_TO_SUBJECT": "REQUIRE_SUBJECT_MATERIAL",
                "FORMAT_LOCK": "TEMPLATE_ONLY",
            }
            return {
                "type": gtype,
                "action": action_map.get(gtype, "SKIP_IF_NO_EVIDENCE"),
            }
    # 默认：需要有证据
    return {
        "type": "EVIDENCE_REQUIRED",
        "action": "SKIP_IF_NO_EVIDENCE",
    }


def bind_guardrails_to_outline(
    outline: list,
    guardrails: list,
) -> list:
    """将生成约束绑定到目录节点。

    绑定策略：
    - MANDATORY_MATCH → 绑定到标题含"技术参数/方案/要求"的节点
    - REQUIRED_SIGNATURE → 绑定到标题含"函/声明/承诺"的节点
    - BIND_TO_SUBJECT → 绑定到标题含"资格/资质/证明"的节点
    - EVIDENCE_REQUIRED → 绑定到标题含"业绩/实施/方案"的节点
    - FORMAT_LOCK → 绑定到强制条款节点

    Args:
        outline: 目录树（每节点含 title/children）
        guardrails: 生成约束列表

    Returns:
        注入 guardrails 后的目录树
    """
    category_map = {
        "MANDATORY_MATCH": ["技术", "参数", "方案"],
        "REQUIRED_SIGNATURE": ["函", "声明", "承诺", "授权"],
        "BIND_TO_SUBJECT": ["资格", "资质", "证明", "执照"],
        "EVIDENCE_REQUIRED": ["业绩", "实施", "方案", "售后", "服务"],
        "FORMAT_LOCK": ["函", "声明", "承诺", "表"],
    }

    for node in _iter_outline_nodes(outline):
        title = node.get("title", "")
        node_guardrails = []
        for guardrail in guardrails:
            gtype = guardrail.get("type", "")
            keywords = category_map.get(gtype, [])
            if any(kw in title for kw in keywords):
                # 去重
                if not any(
                    existing.get("type") == gtype
                    and existing.get("detail") == guardrail.get("detail")
                    for existing in node_guardrails
                ):
                    node_guardrails.append(guardrail)
        if node_guardrails:
            node["guardrails"] = node_guardrails

    return outline


def _iter_outline_nodes(outline):
    """递归遍历目录树的所有节点（含子节点）。"""
    for node in outline:
        yield node
        yield from _iter_outline_nodes(node.get("children", []))
