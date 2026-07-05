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
        # 小于100元不可能是采购预算（可能是数量被误识别为金额）
        if val <= 0 or val > 1e12 or val < 100:
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

    # ── 前置标准化：确保关键字段为 dict 格式 ──
    # project_name / project_code 可能为字符串，需包装为 {"value": str}
    for _field in ("project_name", "project_code"):
        _v = meta.get(_field)
        if not isinstance(_v, dict):
            if isinstance(_v, str) and _v.strip():
                meta[_field] = {"value": _v.strip()}
            else:
                meta[_field] = {}
    # purchaser / agent 可能为字符串，需包装为 {"name": str}
    for _field in ("purchaser", "agent"):
        _v = meta.get(_field)
        if not isinstance(_v, dict):
            if isinstance(_v, str) and _v.strip():
                meta[_field] = {"name": _v.strip()}
            else:
                meta[_field] = {}
    # budget 可能为字符串/数字，需包装为 {"total": int/0}
    _budget_raw = meta.get("budget")
    if not isinstance(_budget_raw, dict):
        if isinstance(_budget_raw, (int, float)) and _budget_raw > 0:
            meta["budget"] = {"total": int(_budget_raw)}
        elif isinstance(_budget_raw, str) and _budget_raw.strip():
            meta["budget"] = {"note": _budget_raw.strip()}
        else:
            meta["budget"] = {}


    # 项目名称（LLM 补充规则未覆盖的封面独立行格式）
    pn_val = meta.get("project_name", {})
    if not isinstance(pn_val, dict) or not pn_val.get("value"):
        llm_pn = llm_meta.get("project_name")
        if llm_pn and len(str(llm_pn).strip()) > 4 and str(llm_pn).strip().lower() not in ("null", "none"):
            meta["project_name"]["value"] = str(llm_pn).strip()

    # 项目编号
    pc_val = meta.get("project_code", {})
    if not isinstance(pc_val, dict) or not pc_val.get("value"):
        llm_pc = llm_meta.get("project_code")
        if llm_pc and str(llm_pc).strip().lower() not in ("null", "none", ""):
            meta["project_code"]["value"] = str(llm_pc).strip()

    # 购买人名称
    pur_val = meta.get("purchaser", {})
    if not isinstance(pur_val, dict) or not pur_val.get("name"):
        llm_name = validate_purchaser_name(llm_meta.get("purchaser_name"))
        if llm_name:
            meta.setdefault("purchaser", {})["name"] = llm_name

    # 购买人联系人
    pur_con = meta.get("purchaser", {})
    if not isinstance(pur_con, dict) or not pur_con.get("contact"):
        llm_contact = llm_meta.get("purchaser_contact")
        if llm_contact and str(llm_contact).strip().lower() not in ("null", "none"):
            meta.setdefault("purchaser", {})["contact"] = str(llm_contact).strip()

    # 代理机构名称
    agt_val = meta.get("agent", {})
    if not isinstance(agt_val, dict) or not agt_val.get("name"):
        llm_name = validate_agent_name(llm_meta.get("agent_name"))
        if llm_name:
            meta.setdefault("agent", {})["name"] = llm_name

    # 代理机构联系人
    agt_con = meta.get("agent", {})
    if not isinstance(agt_con, dict) or not agt_con.get("contact"):
        llm_contact = llm_meta.get("agent_contact")
        if llm_contact and str(llm_contact).strip().lower() not in ("null", "none"):
            meta.setdefault("agent", {})["contact"] = str(llm_contact).strip()

    # 预算
    llm_budget = llm_meta.get("budget", {}) if isinstance(llm_meta.get("budget"), dict) else {}
    if isinstance(llm_budget, dict):
        bdgt_raw = meta.get("budget", {})
        current_total = bdgt_raw.get("total", 0) if isinstance(bdgt_raw, dict) else 0
        if not current_total or current_total == 0:
            llm_total = validate_budget_total(llm_budget.get("budget_total", 0))
            if llm_total > 0:
                meta.setdefault("budget", {})["total"] = llm_total

        # 保存预算原文描述（如"据实结算""无预算"等非数值描述）
        llm_note = llm_budget.get("budget_note", "")
        if llm_note and str(llm_note).strip().lower() not in ("", "null", "none"):
            meta.setdefault("budget", {})["note"] = str(llm_note).strip()

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
