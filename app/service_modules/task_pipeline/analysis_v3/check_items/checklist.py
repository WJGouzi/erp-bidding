"""扁平化 checklist 模块：聚合资格/评分/废标的待确认项。"""
import json
import logging

from app.domain.models import BiddingCheckItem

logger = logging.getLogger(__name__)


def _get_confirmed_status(shared_resource_id: int, check_key: str) -> bool:
    """从 BiddingCheckItem 表读取确认状态。"""
    item = BiddingCheckItem.query.filter_by(
        shared_resource_id=shared_resource_id,
        check_key=check_key,
    ).first()
    return item.confirmed_flag if item else False


def _safe_get(obj, key, default=""):
    """安全获取嵌套值。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def assemble_checklist(result, analysis: dict) -> list:
    """从资格/评分/废标聚合待确认项，扁平输出。"""
    items = []
    sr_id = result.shared_resource_id

    eligibility = analysis.get("eligibility", {})
    if isinstance(eligibility, str):
        try:
            eligibility = json.loads(eligibility)
        except (json.JSONDecodeError, TypeError):
            eligibility = {}

    scoring = analysis.get("scoring", {})
    if isinstance(scoring, str):
        try:
            scoring = json.loads(scoring)
        except (json.JSONDecodeError, TypeError):
            scoring = {}

    # ── 1. 资格要求（通用资格）──
    for q in eligibility.get("qualifications", []):
        qid = q.get("id", f"qual_{len(items)}")
        key = f"qual_{qid}"
        items.append({
            "check_key": key,
            "category": "qualification",
            "severity": q.get("severity", "critical"),
            "content": q.get("requirement", ""),
            "prep_guide": q.get("material", q.get("required_material", "")),
            "confirmed": _get_confirmed_status(sr_id, key),
        })

    # ── 2. ★ 实质性要求 ──
    for s in eligibility.get("starred_requirements", []):
        sid = s.get("id", f"star_{len(items)}")
        key = f"star_{sid}"
        items.append({
            "check_key": key,
            "category": "compliance",
            "severity": "fatal",
            "content": s.get("requirement", ""),
            "prep_guide": s.get("material", ""),
            "confirmed": _get_confirmed_status(sr_id, key),
        })

    # ── 3. 废标条件 ──
    for d in eligibility.get("disqualifications", []):
        did = d.get("id", f"disq_{len(items)}")
        key = f"disq_{did}"
        items.append({
            "check_key": key,
            "category": "must_pass",
            "severity": "fatal",
            "content": d.get("requirement", ""),
            "prep_guide": d.get("detail", d.get("description", "")),
            "confirmed": _get_confirmed_status(sr_id, key),
        })

    # ── 4. 评分维度（主观/半客观需要确认）──
    for dim in scoring.get("dimensions", []):
        if isinstance(dim, str):
            try:
                dim = json.loads(dim)
            except (json.JSONDecodeError, TypeError):
                continue
        dim_type = (dim.get("type") or "").lower()
        if dim_type in ("subjective", "semi_objective"):
            dim_name = dim.get("name", "")
            key = f"score_dim_{dim_name}"
            items.append({
                "check_key": key,
                "category": "scoring",
                "severity": "normal",
                "content": f"{dim_name}（{dim.get('score', 0)}分）",
                "prep_guide": dim.get("criteria", dim.get("standard", "")),
                "confirmed": _get_confirmed_status(sr_id, key),
            })

    # ── 5. 排序 ──
    severity_order = {"fatal": 0, "critical": 1, "normal": 2, "optional": 3}
    items.sort(key=lambda x: (severity_order.get(x["severity"], 99), x["content"]))

    return items
