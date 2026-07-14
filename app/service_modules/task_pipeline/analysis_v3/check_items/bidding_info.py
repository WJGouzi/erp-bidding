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


def _extract_budget(meta, selected_package_no=0) -> dict:
    """从 metadata 提取预算信息，支持分包预算三级降级。

    降级链：
      1. budget.packages[str(package_no)] → 所选包的预算
      2. budget.total → 项目总预算
      3. 0 → 无预算信息
    
    Args:
        meta: analysis_data.metadata
        selected_package_no: 当前选中的包号（0 表示未选择或无分包）
    """
    budget_raw = meta.get("budget", 0)
    if not isinstance(budget_raw, dict):
        if isinstance(budget_raw, (int, float)):
            return {"total": budget_raw, "note": ""}
        return {"total": 0, "note": str(budget_raw)}

    # 第1级：查包预算
    if selected_package_no > 0:
        packages = budget_raw.get("packages", {})
        if isinstance(packages, dict):
            pkg_budget = packages.get(str(selected_package_no))
            if pkg_budget is not None and str(pkg_budget).strip():
                return {
                    "total": str(pkg_budget),
                    "note": budget_raw.get("note", ""),
                }

    # 第2级：降级到项目总预算
    total = budget_raw.get("total", 0)
    if total is not None and str(total).strip() and str(total) != "0":
        return {
            "total": str(total),
            "note": budget_raw.get("note", ""),
        }

    # 第3级：无预算信息
    return {"total": 0, "note": budget_raw.get("note", "")}


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

    # bid_deadline/bid_open_time 优先从顶层读取，fallback 到 key_dates 子结构
    _bid_dl = meta.get("bid_deadline", "") or ""
    if not _bid_dl:
        _kd = meta.get("key_dates", {})
        if isinstance(_kd, dict):
            _bid_dl = _kd.get("bid_deadline", "") or ""
    _bid_ot = meta.get("bid_open_time", "") or ""
    if not _bid_ot:
        _kd2 = meta.get("key_dates", {})
        if isinstance(_kd2, dict):
            _bid_ot = _kd2.get("bid_opening", "") or ""

    return {
        "project_name": meta.get("project_name", {}).get("value", "") if isinstance(meta.get("project_name"), dict) else (meta.get("project_name") or ""),
        "project_code": meta.get("project_code", {}).get("value", "") if isinstance(meta.get("project_code"), dict) else (meta.get("project_code") or ""),
        "package_no": pkg_info["package_no"],
        "package_name": pkg_info["package_name"],
        "budget": _extract_budget(meta, selected_package_no=pkg_info["package_no"]),
        "purchaser": _extract_name(meta.get("purchaser", "")),
        "agency": _extract_name(meta.get("agent", "")),
        "domain": meta.get("domain", ""),
        "summary": getattr(result, "computed_overview", None) or result.overview or "",
        "sme_only": meta.get("sme_only", False),
        "dark_bid": meta.get("dark_bid", False),
        "bid_deadline": _bid_dl,
        "bid_bond": meta.get("bid_bond", ""),
        "bid_open_time": _bid_ot,
    }
