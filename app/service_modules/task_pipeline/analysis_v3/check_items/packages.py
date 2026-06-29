"""分包信息模块：从 packages_json 组装。"""
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


def _get_selected_package_no(result) -> int:
    """从 shared_resource 获取已选包号（带降级）。"""
    try:
        from app.domain.models import BiddingSharedResource
        sr = BiddingSharedResource.query.get(result.shared_resource_id)
        if sr and sr.selected_package_no:
            try:
                return int(sr.selected_package_no)
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    return 0


def _is_clause_text(text: str) -> bool:
    """判断文本是否像条款内容而非包名。"""
    if not text:
        return True
    # 以数字+点开头（如 "28.1"）、或以"经"、"如"等连词开头
    clause_patterns = [
        r"^\d+[\.\、\s]",
        r"^经\w+",
        r"^如\w+",
        r"^在\w+",
        r"^本\w+",
    ]
    import re
    for pat in clause_patterns:
        if re.match(pat, text):
            return True
    if len(text) > 60:  # 包名通常不超过30字
        return True
    return False


def _clean_package_name(pkg: dict, project_name: str) -> str:
    """清理包名：如果包名像条款内容，用项目名替代。"""
    raw_name = pkg.get("name", "") or ""
    if not raw_name or _is_clause_text(raw_name):
        if project_name:
            return project_name
        return f"第{pkg.get('package_no', 1)}包"
    return raw_name


def assemble_packages(result, analysis: dict) -> dict:
    """组装分包信息。"""
    pkgs = _safe_load_json(result.packages_json)

    selected_no = _get_selected_package_no(result)
    
    meta = analysis.get("metadata", {})
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = {}
    project_name = meta.get("project_name", {}).get("value", "") if isinstance(meta.get("project_name"), dict) else (meta.get("project_name") or "")

    # 清理每个包的名称
    cleaned = []
    for pkg in pkgs:
        cleaned_pkg = dict(pkg)
        cleaned_pkg["name"] = _clean_package_name(pkg, project_name)
        cleaned.append(cleaned_pkg)

    return {
        "has_packages": len(pkgs) > 1,
        "current_package_no": selected_no,
        "packages": cleaned,
    }
