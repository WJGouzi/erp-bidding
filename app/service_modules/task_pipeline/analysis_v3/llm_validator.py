"""LLM 输出校验 + 兜底逻辑。

每个验证函数检查 LLM 输出的合理性，不符合预期的值用规则兜底。
"""

import re
import logging

logger = logging.getLogger(__name__)


def validate_purchaser_name(name):
    """校验购买人名称是否合理。"""
    if not name:
        return None
    name = name.strip()
    # 太短的不合理
    if len(name) < 4:
        return None
    # 包含明显非名称内容
    if re.search(r'(null|None|未找到|未知|无)', name, re.IGNORECASE):
        return None
    return name


def validate_agent_name(name):
    """校验代理机构名称是否合理。"""
    if not name:
        return None
    name = name.strip()
    if len(name) < 4:
        return None
    if re.search(r'(null|None|未找到|未知|无)', name, re.IGNORECASE):
        return None
    return name


def validate_budget_total(amount):
    """校验预算金额是否合理。"""
    try:
        val = float(amount)
        if val <= 0 or val > 1e12:  # 超过万亿不合理
            return 0
        return int(val)
    except (TypeError, ValueError):
        return 0


def validate_packages(packages, package_count):
    """校验分包预算是否与包数一致。"""
    if not packages:
        return []
    if not isinstance(packages, list):
        return []
    # 包数量不能超过检测到的包数太多
    if package_count > 0 and len(packages) > package_count * 2:
        return []
    return [p for p in packages if isinstance(p, dict) and p.get("package_no")]


def merge_llm_into_metadata(rule_meta, llm_meta):
    """将 LLM 提取的元数据合并到规则结果中。

    原则：规则有值则保留规则值（规则更精确），规则空值则用 LLM 值。
    """
    if not llm_meta:
        return rule_meta

    meta = dict(rule_meta) if rule_meta else {}

    # 项目名称（LLM 补充规则未覆盖的封面独立行格式）
    if not meta.get("project_name"):
        llm_pn = llm_meta.get("project_name")
        if llm_pn and len(str(llm_pn).strip()) > 4 and str(llm_pn).strip().lower() not in ("null", "none"):
            meta["project_name"] = str(llm_pn).strip()

    # 项目编号
    if not meta.get("project_code"):
        llm_pc = llm_meta.get("project_code")
        if llm_pc and str(llm_pc).strip().lower() not in ("null", "none", ""):
            meta["project_code"] = str(llm_pc).strip()

    # 购买人名称
    if not meta.get("purchaser", {}).get("name"):
        llm_name = validate_purchaser_name(llm_meta.get("purchaser_name"))
        if llm_name:
            meta.setdefault("purchaser", {})["name"] = llm_name

    # 购买人联系人
    if not meta.get("purchaser", {}).get("contact"):
        llm_contact = llm_meta.get("purchaser_contact")
        if llm_contact and str(llm_contact).strip().lower() not in ("null", "none"):
            meta.setdefault("purchaser", {})["contact"] = str(llm_contact).strip()

    # 代理机构名称
    if not meta.get("agent", {}).get("name"):
        llm_name = validate_agent_name(llm_meta.get("agent_name"))
        if llm_name:
            meta.setdefault("agent", {})["name"] = llm_name

    # 代理机构联系人
    if not meta.get("agent", {}).get("contact"):
        llm_contact = llm_meta.get("agent_contact")
        if llm_contact and str(llm_contact).strip().lower() not in ("null", "none"):
            meta.setdefault("agent", {})["contact"] = str(llm_contact).strip()

    # 预算
    llm_budget = llm_meta.get("budget", {}) if isinstance(llm_meta.get("budget"), dict) else {}
    if isinstance(llm_budget, dict):
        current_total = meta.get("budget", {}).get("total", 0)
        if not current_total or current_total == 0:
            llm_total = validate_budget_total(llm_budget.get("budget_total", 0))
            if llm_total > 0:
                meta.setdefault("budget", {})["total"] = llm_total

        # 分包预算（规则层没有分包预算的概念，LLM 有则补充）
        llm_packages = validate_packages(
            llm_budget.get("packages", []),
            meta.get("package_count", 0)
        )
        if llm_packages:
            meta.setdefault("budget", {})["packages"] = {}
            for pkg in llm_packages:
                pno = str(pkg.get("package_no", ""))
                if pno:
                    meta["budget"]["packages"][pno] = pkg.get("amount", 0)

    return meta
