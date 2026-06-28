"""投标人须知模块：从 metadata + overview 组装。"""
import json
import logging

logger = logging.getLogger(__name__)


def _get_selected_package_no(result) -> str:
    """获取当前选中的包号。"""
    pkgs = _safe_load_json(result.packages_json)
    if pkgs and isinstance(pkgs, list) and len(pkgs) > 0:
        # 取第一个包作为默认（包选择逻辑在外部处理）
        first = pkgs[0]
        return first.get("name", f"第{first.get('package_no', 1)}包")
    return ""


def _safe_load_json(val):
    if not val:
        return None
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val) if isinstance(val, str) else val
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_name(val):
    """从 metadata 字段提取纯字符串名称。
    
    支持格式：
    - 字符串: 直接返回
    - dict: 提取 name 字段
    - 其他: 返回空字符串
    """
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("name", "")
    return ""


def _get_current_package_info(result) -> dict:
    """获取当前选中的分包信息（包号和包名）。"""
    pkgs = _safe_load_json(result.packages_json)
    if not pkgs or not isinstance(pkgs, list) or len(pkgs) == 0:
        return {"package_no": 0, "package_name": ""}
    
    # 尝试从 shared_resource 获取已选包号
    try:
        from app.domain.models import BiddingSharedResource
        sr = BiddingSharedResource.query.get(result.shared_resource_id)
        if sr and sr.selected_package_no:
            try:
                selected = int(sr.selected_package_no)
                for pkg in pkgs:
                    if pkg.get("package_no") == selected:
                        return {
                            "package_no": selected,
                            "package_name": pkg.get("name", f"第{selected}包"),
                        }
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    
    # 默认取第一个包
    first = pkgs[0]
    return {
        "package_no": first.get("package_no", 0),
        "package_name": first.get("name", ""),
    }


def _extract_budget(meta) -> dict:
    """从 metadata 提取预算信息。"""
    budget_raw = meta.get("budget", 0)
    if isinstance(budget_raw, dict):
        return {
            "total": budget_raw.get("total", 0),
            "note": budget_raw.get("note", ""),
        }
    if isinstance(budget_raw, (int, float)):
        return {"total": budget_raw, "note": ""}
    return {"total": 0, "note": str(budget_raw)}


def assemble_bidding_info(result, analysis: dict) -> dict:
    """组装投标人须知部分。

    数据来源优先级：analysis_data.metadata > 独立字段
    """
    meta = analysis.get("metadata", {})

    # metadata 可能是字符串，尝试解析
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}

    pkg_info = _get_current_package_info(result)

    return {
        "project_name": meta.get("project_name", ""),
        "project_code": meta.get("project_code", ""),
        "package_no": pkg_info["package_no"],
        "package_name": pkg_info["package_name"],
        "budget": _extract_budget(meta),
        "purchaser": _extract_name(meta.get("purchaser", "")),
        "agency": _extract_name(meta.get("agent", "")),
        "domain": meta.get("domain", ""),
        "summary": (result.overview or ""),
        "sme_only": meta.get("sme_only", False),
        "dark_bid": meta.get("dark_bid", False),
        "bid_deadline": meta.get("bid_deadline", ""),
        "bid_bond": meta.get("bid_bond", ""),
        "bid_open_time": meta.get("bid_open_time", ""),
    }
