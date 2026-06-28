"""资格审查模块：三 Tab 分组 — 资格性审查 / 符合性审查 / 废标项。"""
import json
import logging

logger = logging.getLogger(__name__)


def _safe_load_json(val):
    if not val:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val) if isinstance(val, str) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _extract_qual_items(eligibility: dict, result) -> list:
    """从 eligibility + qualification_requirements 提取资格性审查项。"""
    items = []

    # 从 analysis_data.eligibility.qualifications 提取
    for q in eligibility.get("qualifications", []):
        items.append({
            "id": q.get("id", ""),
            "requirement": q.get("requirement", ""),
            "material": q.get("material", q.get("required_material", "")),
            "status": q.get("status", "pending"),
        })

    # 从独立字段 qualification_requirements 补充
    qual_reqs = _safe_load_json(result.qualification_requirements)
    if qual_reqs:
        existing = {item["id"] for item in items}
        for q in qual_reqs:
            qid = q.get("id", "")
            if qid not in existing:
                items.append({
                    "id": qid,
                    "requirement": q.get("requirement", q.get("item", "")),
                    "material": q.get("material", q.get("required_material", "")),
                    "status": "pending",
                })

    return items


def _extract_compliance_items(eligibility: dict) -> list:
    """从 eligibility 提取符合性审查项。"""
    items = []
    # 从 eligibility 中的 starred_requirements 提取
    for s in eligibility.get("starred_requirements", []):
        items.append({
            "id": s.get("id", f"star_{len(items)}"),
            "requirement": s.get("requirement", ""),
            "material": s.get("material", ""),
            "status": "attention",
        })
    return items


def _extract_rejection_items(eligibility: dict, result) -> list:
    """从 disqualification_items + eligibility 提取废标项。"""
    items = []

    # 从 analysis_data.eligibility.disqualifications 提取
    for d in eligibility.get("disqualifications", []):
        items.append({
            "id": d.get("id", ""),
            "item": d.get("requirement", ""),
            "detail": d.get("detail", d.get("description", "")),
            "severity": "fatal",
        })

    # 从独立字段 disqualification_items 补充
    disq = _safe_load_json(result.disqualification_items)
    if disq:
        existing = {item["id"] for item in items}
        for d in disq:
            did = d.get("id", "")
            if did not in existing:
                items.append({
                    "id": did,
                    "item": d.get("item", d.get("requirement", "")),
                    "detail": d.get("detail", d.get("description", "")),
                    "severity": "fatal",
                })

    return items


def assemble_qualification(result, analysis: dict) -> dict:
    """组装资格审查（三 Tab）。"""
    eligibility = analysis.get("eligibility", {})
    if isinstance(eligibility, str):
        try:
            eligibility = json.loads(eligibility)
        except (json.JSONDecodeError, TypeError):
            eligibility = {}

    return {
        "qualification_items": _extract_qual_items(eligibility, result),
        "compliance_items": _extract_compliance_items(eligibility),
        "rejection_items": _extract_rejection_items(eligibility, result),
    }
