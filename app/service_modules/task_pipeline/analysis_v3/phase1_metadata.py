"""Phase 1: 元数据提取 — 纯规则+表格融合，零LLM。

提取的元数据字段：
  - 项目名称/编号（project_name, project_code）
  - 采购人/代理机构（purchaser, agent）
  - 预算（budget.total / budget.packages）
  - 关键日期（bid_deadline, bid_opening 等）
  - 商务条款（付款方式、服务期限、验收标准等）
  - 文档类型分类（TENDER/SELECTION/NEGOTIATION/INQUIRY）
"""

import logging
import re
from collections import OrderedDict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  元数据字段 → JSON 路径映射（按提取优先级排序）
#  同一字段可有多条规则（处理不同书写格式）
# ═══════════════════════════════════════════════════════════════

KEY_MAP = OrderedDict([
    # ── 项目基本信息 ──
    ("project_code", ("project_code", "value")),
    ("project_name", ("project_name", "value")),
    ("purchaser_name", ("purchaser", "name")),
    ("agent_name", ("agent", "name")),
    ("purchaser_name_cn", ("purchaser", "name")),
    ("project_code_old", ("project_code", "value")),
    ("project_name_old", ("project_name", "value")),
    # ── 预算 ──
    ("budget", ("budget", "total")),
    ("budget_cn", ("budget", "total")),
    ("budget_item", ("budget", "total")),
    ("package_budget", ("budget", "packages")),
    # ── 关键日期 ──
    ("bid_deadline", ("key_dates", "bid_deadline")),
    ("bid_opening", ("key_dates", "bid_opening")),
    ("bid_validity", ("key_dates", "bid_validity_days")),
    ("file_purchase_period", ("key_dates", "file_purchase_start")),
    # ── 商务条款 ──
    ("payment_terms", ("extra", "payment_terms")),
    ("service_period", ("extra", "service_period")),
    ("delivery_location", ("extra", "delivery_location")),
    ("acceptance_standard", ("extra", "acceptance_standard")),
    ("pricing_rule", ("extra", "pricing_rule")),
    ("special_declaration", ("extra", "special_declaration")),
    ("agency_fee", ("extra", "agency_fee")),
    ("agency_fee_cn", ("extra", "agency_fee")),
    ("file_purchase_price", ("extra", "file_purchase_price")),
    ("bid_submission_location", ("extra", "bid_submission_location")),
    ("winner_count", ("extra", "winner_count_text")),
    ("submission_copies", ("extra", "submission_copies")),
    ("submission_docs", ("extra", "submission_docs_summary")),
    ("submission_copy_detail", ("extra", "submission_copy_detail")),
    ("warranty_period", ("extra", "warranty_period")),
    ("pkg_special_qual", ("extra", "pkg_special_qual")),
    # ── 评标方法 ──
    ("evaluation_method", ("evaluation_method", "value")),
])


# ═══════════════════════════════════════════════════════════════
#  规则集（按优先级排序）
#  (字段键名, 正则模式, 优先级, 处理函数)
# ═══════════════════════════════════════════════════════════════

RULES = [
    # ── 项目名称/编号 ──
    ("project_code", r"项目编号[：:]\s*([A-Z0-9]+[A-Z0-9\-]+[A-Z0-9])", 1, "identity"),
    ("project_code", r"(?:采购|比选)编号[：:]\s*([\w\-]+)", 2, "identity"),
    ("project_name", r"项目名称[：:]\s*(.+?)(?:\s{2,}|$|（|\()", 1, "identity"),
    ("project_name", r"项目名称[：:]\s*(.+?)(?:。|；|\n|$)", 2, "identity"),
    ("project_name", r"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{2,}(?:采购|服务|工程)项目)(?![\u4e00-\u9fff])", 3, "identity"),

    # ── 采购人/代理 ──
    ("purchaser_name", r"(?:采购人|比选人|招标人)[：:]\s*(.+?)(?:\s{2,}|联系电话|地址|$|（|\(|联系人|。)", 1, "identity"),
    ("purchaser_name", r"(?:采购人|比选人|招标人)\s*[:：]\s*(.+?)$", 2, "identity"),
    ("agent_name", r"(?:采购代理机构|比选代理机构|招标代理机构|代理机构)[：:]\s*(.+?)(?:\s{2,}|联系电话|地址|$|（|\(|联系人|。)", 1, "identity"),
    ("agent_name", r"(?:采购代理机构|比选代理机构|招标代理机构|代理机构)\s*[:：]\s*(.+?)$", 2, "identity"),

    # ── 预算 ──
    ("budget", r"(?:采购预算|预算金额|项目预算|最高限价|控制价)[：:]*[^。]*?([\d,]+(?:\.[\d]+)?)\s*(?:万元|元)", 1, "parse_money"),
    ("budget_cn", r"(?:采购预算|预算金额|项目预算|最高限价)[^。]*?([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+)\s*元", 1, "parse_money_cn"),
    ("budget_item", r"预算[：:]*[^。]*?([\d,]+(?:\.[\d]+)?)\s*(?:万元|元)", 2, "parse_money"),
    ("package_budget", r"第[\d一二三四五六七八九十]+包[：:][^。]*?([\d,]+(?:\.[\d]+)?)\s*(?:万元|元)", 1, "parse_money"),

    # ── 关键日期 ──
    ("bid_deadline", r"(?:投标截止|递交投标文件截止|投标截止时间|递交的起止时间|递交比选申请书截止时间)[^。]*?([\d]{4}年[\d]{1,2}月[\d]{1,2}日)", 1, "identity"),
    ("bid_opening", r"(?:开标时间|比选申请书开启时间|开启时间)[^。]*?([\d]{4}年[\d]{1,2}月[\d]{1,2}日\s*[\d]{1,2}:[\d]{2})", 1, "identity"),
    ("file_purchase_period", r"(?:采购文件|比选文件|招标文件)\s*(?:自|从)\s*([\d]{4}年[\d]{1,2}月[\d]{1,2}日)\s*至\s*([\d]{4}年[\d]{1,2}月[\d]{1,2}日)", 1, "file_purchase_period"),
    ("bid_validity", r"(?:投标有效期|磋商有效期)[^。]*?([\d]+)\s*天", 1, "identity"),

    # ── 商务条款 ──
    ("payment_terms", r"(?:付款方法|付款方式|支付方式|付款条件|结算方式)[：:]\s*(.+?)(?:。|；|\n{2,}|\n(?:[^\n]*[：:]|$))", 1, "identity"),
    ("service_period", r"(?:服务期限|交货期限|交货期|供货期|工期)[：:]*[^。]*?([\d]+)\s*(?:年|月|天|日|个?月)", 1, "parse_number"),
    ("delivery_location", r"(?:配送地点|交货地点|交付地点|供货地点)[：:]\s*(.+?)(?:。|；|\n{2,}|$)", 1, "identity"),
    ("acceptance_standard", r"(?:验收标准|验收方式|验收要求)[：:]\s*(.+?)(?:。|；|\n{2,}|$)", 1, "identity"),
    ("pricing_rule", r"(?:报价|本次报价|报价方式)[^。]*?(一次性报价|多轮报价|据实结算|固定单价|固定总价|综合单价|包干价|按实结算)", 1, "identity"),
    ("special_declaration", r"(?:声明|特别说明)[：:]\s*(.+?)(?:。|；|$)", 1, "identity"),
    ("agency_fee", r"(?:代理服务费|招标代理服务费|代理费)[^。]*?([\d]+)\s*元", 1, "parse_number"),
    ("agency_fee_cn", r"(?:代理服务费|招标代理服务费|代理费)[^。]*?[共]?[^。]*?(?:人民币)?([零壹贰叁肆伍陆柒捌玖拾佰仟万]+)\s*元", 1, "parse_money_cn"),
    ("file_purchase_price", r"(?:采购文件|比选文件|招标文件)\s*售价[：:][^。]*?([\d]+)\s*元", 1, "parse_number"),
    ("bid_submission_location", r"(?:递交比选申请书|递交投标文件|提交投标文件)(?:地点|截止时间前送达|的截止时间前送达)[：:]*\s*(.+?)(?:。|；|$)", 1, "identity"),
    ("winner_count", r"(?:确定[\d一二三四五六七八九十]+家|选择[\d一二三四五六七八九十]+家|选取[\d一二三四五六七八九十]+家)", 1, "parse_winner_count"),
    ("submission_copies", r"(?:投标文件|响应文件|比选申请书)[^。]*?(?:正本|副本)\s*([\d]+)\s*份", 1, "parse_number"),
    ("submission_docs", r"(?:比选申请书|响应文件|投标文件)(?:组成|主要包括)[：:：][^。]*?(?:比选申请函|法定代表人|授权委托书|报价|承诺函|资格证明)", 1, "identity"),

    # ── 评标方法 ──
    ("evaluation_method", r"(?:评标办法|比选方法|评审方法)[：:]*\s*(综合评分法|最低评标价法|综合评估法)", 1, "identity"),
    ("evaluation_method", r"(?:评标办法|比选方法|评审方法)\s*[|]\s*(综合评分法|最低评标价法|综合评估法)", 2, "identity"),
]


# 必填字段列表（元数据中必须包含的字段）
REQUIRED_FIELDS = [
    "project_name", "project_code",
    "purchaser.name", "agent.name",
    "budget.total", "bid_type",
    "document_type",
]


def parse_money(text):
    """解析金额文本，返回 float 金额（单位：元）。"""
    text = str(text).strip()
    # 是否包含"万元"单位
    is_wan = "万" in text
    # 去除千分位逗号
    text = text.replace(",", "").replace("，", "")
    # 提取数字
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        value = float(m.group(1))
        if is_wan:
            value *= 10000
        return value
    return None


def parse_number(text):
    """解析纯数字。"""
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return None


def parse_winner_count(text):
    """解析中标人家数文本。"""
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for k, v in mapping.items():
        if k in text:
            return v
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return None


def parse_money_cn(text):
    """解析中文大写金额。"""
    cn_map = {"零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4,
              "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
              "拾": 10, "佰": 100, "仟": 1000, "万": 10000}
    # 简化处理：只提取数字部分
    m = re.search(r"([零壹贰叁肆伍陆柒捌玖拾佰仟万]+)", text)
    if not m:
        return None
    cn_str = m.group(1)
    total = 0
    current = 0
    for ch in cn_str:
        if ch in cn_map:
            v = cn_map[ch]
            if v >= 10:
                if current == 0:
                    current = 1
                total += current * v
                current = 0
            else:
                current = v
    total += current
    return float(total) if total > 0 else None


def _rule_extract(text):
    """执行正则规则提取。

    Args:
        text: 纯文本（文档前 N 字符）

    Returns:
        dict: {field_key: value, "_confidence": {field_key: {"confidence": float, "source": str}}}
    """
    result = {}
    confidence_info = {}  # field_key -> {"confidence": float, "source": str}
    seen_fields = {}  # field_key -> priority

    for field_key, pattern, priority, processor in RULES:
        # 如果同字段已有更高优先级的匹配，跳过
        if field_key in seen_fields and seen_fields[field_key] < priority:
            continue

        m = re.search(pattern, text)
        if m:
            value = m.group(1) if m.lastindex else m.group(0).strip()
            if processor == "parse_money":
                value = parse_money(value)
            elif processor == "parse_money_cn":
                value = parse_money_cn(value)
            elif processor == "parse_number":
                value = parse_number(value)
            elif processor == "parse_winner_count":
                value = parse_winner_count(value)
            elif processor == "file_purchase_period":
                value = m.group(1) if m.lastindex and m.lastindex >= 1 else value
            if value is not None and (isinstance(value, str) and len(value) >= 2 or not isinstance(value, str)):
                result[field_key] = value
                seen_fields[field_key] = priority
                # 记录置信度
                base_conf = {1: 0.90, 2: 0.85, 3: 0.75, 4: 0.70}.get(priority, 0.7)
                confidence_info[field_key] = {
                    "confidence": base_conf,
                    "source": f"regex:rule_{field_key}_p{priority}",
                }

    result["_confidence"] = confidence_info
    return result


def _build_metadata(rule_result):
    """将规则提取结果组装为 metadata dict。（含 _confidence 键）

    Args:
        rule_result: dict from _rule_extract()

    Returns:
        metadata dict（标准结构）
    """
    confidence_info = rule_result.pop("_confidence", {})
    metadata = {
        "project_name": {"value": ""},
        "project_code": {"value": ""},
        "purchaser": {"name": "", "alias": "", "contact": ""},
        "agent": {"name": "", "contact": ""},
        "budget": {"total": 0, "note": "", "packages": {}},
        "key_dates": {
            "bid_deadline": "", "bid_opening": "",
            "bid_validity_days": 90,
            "file_purchase_start": "", "file_purchase_end": "",
        },
        "extra": {
            "file_purchase_price": 0,
            "bid_submission_location": "",
            "special_declaration": "",
            "agency_fee": 0,
            "winner_count_text": "",
            "acceptance_standard": "",
            "pricing_rule": "",
            "submission_copies": 0,
            "service_period": 0,
            "delivery_location": "",
            "payment_terms": "",
            "warranty_period": 0,
            "submission_docs_summary": "",
            "submission_copy_detail": "",
            "pkg_special_qual": "",
        },
        "evaluation_method": {"value": ""},
        "allow_consortium": False,
        "allow_subcontracting": False,
        "bid_security_required": False,
        "performance_security_pct": 0,
        "package_count": 0,
        "document_type": {"value": "TENDER", "confidence": "low", "source": "default"},
        "tables": {},
    }

    # 填充规则提取结果
    for key, (section, field) in KEY_MAP.items():
        if key in rule_result:
            value = rule_result[key]
            target = metadata
            for part in section.split("."):
                if isinstance(target, dict):
                    if part not in target:
                        target[part] = {}
                    target = target[part]
                else:
                    break
            if isinstance(target, dict):
                target[field] = value
                # 传播置信度信息
                if key in confidence_info:
                    ci = confidence_info[key]
                    if isinstance(target, dict):
                        target["_confidence"] = ci["confidence"]
                        target["_source"] = ci["source"]
                    elif isinstance(target.get(field), dict):
                        target[field]["_confidence"] = ci["confidence"]
                        target[field]["_source"] = ci["source"]

    # 处理 bundle 字段
    if "package_budget" in rule_result:
        metadata["budget"]["packages"] = rule_result.get("package_budget", {})

    # 文档类型分类结果（优先级高于默认值）
    if "_document_type" in rule_result:
        metadata["document_type"] = rule_result["_document_type"]

    return metadata


def extract_metadata(doc_text, file_name="", table_results=None, sections=None):
    """提取固定元数据字段 — 纯规则+章节提取，零LLM。

    Args:
        doc_text: 文档前部文本（封面+投标邀请+须知前附表，约3000-5000字）
        file_name: 文件名（用于文档类型分类）
        table_results: 表格解析结果（可选），用于覆盖/补充 regex 提取
        sections: 文档章节树（可选），用于章节结构提取

    Returns:
        metadata dict（标准结构）
    """
    rule_result = _rule_extract(doc_text)

    # 文档分类
    doc_type = classify_document(file_name, doc_text)
    rule_result["_document_type"] = doc_type

    # 表格解析结果融合（表格优先）
    table_values = {}
    if table_results:
        t = table_results.get("preliminary", {})
        if t.get("evaluation_method"):
            table_values["evaluation_method"] = t["evaluation_method"]
        if "allow_consortium" in t:
            table_values["allow_consortium"] = t["allow_consortium"]
        if "bid_security_required" in t:
            table_values["bid_security_required"] = t["bid_security_required"]
        if t.get("agency_fee"):
            table_values["agency_fee"] = t["agency_fee"]
    rule_result["_table_values"] = table_values

    metadata = _build_metadata(rule_result)

    # 前附表键值对融合（表格优先于 regex）
    if table_results:
        classification = table_results.get("_classification", {}) if isinstance(table_results, dict) else None
        if classification and classification.get("preliminary"):
            metadata = _merge_preliminary_table(metadata, table_results)
        elif table_results.get("preliminary", {}):
            metadata = _merge_preliminary_table(metadata, table_results)

    # 附加表格原始数据
    if table_results:
        metadata["tables"] = {}

    # 章节提取（sections 不为空时，章节结果优先于 regex）
    if sections:
        try:
            from ....infrastructure.section_extractor import extract_business_from_sections
            section_biz = extract_business_from_sections(sections)
            if section_biz:
                for key, value in section_biz.items():
                    if key == "business_terms_raw":
                        continue
                    if value and key in metadata.get("extra", {}):
                        if len(str(value)) > 3:
                            metadata["extra"][key] = str(value)
                    elif value:
                        if "extra" not in metadata:
                            metadata["extra"] = {}
                        metadata["extra"][key] = str(value)
        except Exception as exc:
            logger.warning("[phase1] 章节提取异常: %s", exc)

    return metadata


def classify_document(file_name, doc_text):
    """三层文档分类：文件名 → 正文关键词 → 置信度。

    Args:
        file_name: 文件名（含扩展名）。
        doc_text: 文档正文前 N 字符。

    Returns:
        {"value": "TENDER"|"SELECTION"|"NEGOTIATION"|"INQUIRY",
         "confidence": "high"|"medium"|"low",
         "source": "..."}
    """
    import os
    file_basename = os.path.splitext(os.path.basename(file_name or ""))[0].lower()
    body_sample = (doc_text or "")[:3000]

    # 第一层：文件名判定
    filename_type = None
    if "比选" in file_basename:
        filename_type = "SELECTION"
    elif "竞争性谈判" in file_basename or "竞争性磋商" in file_basename:
        filename_type = "NEGOTIATION"
    elif "询价" in file_basename:
        filename_type = "INQUIRY"

    # 第二层：正文关键词确认
    body_type = None
    body_matches = 0
    type_keywords = {
        "SELECTION": ["比选公告", "比选文件", "比选邀请", "比选须知"],
        "TENDER": ["招标公告", "招标文件", "投标邀请", "投标须知", "公开招标"],
        "NEGOTIATION": ["竞争性谈判公告", "竞争性谈判文件", "谈判邀请",
                         "竞争性磋商公告", "竞争性磋商文件", "磋商邀请", "磋商公告"],
        "INQUIRY": ["询价公告", "询价通知书", "询价邀请"],
    }
    for dtype, kws in type_keywords.items():
        count = sum(1 for kw in kws if kw in body_sample)
        if count > body_matches:
            body_matches = count
            body_type = dtype

    # 第三层：综合判定 + 置信度
    if body_type and filename_type and body_type == filename_type:
        return {
            "value": body_type,
            "confidence": "high",
            "source": f"filename:{filename_type}+body:{body_type}",
        }
    elif body_type:
        return {
            "value": body_type,
            "confidence": "high" if body_matches >= 2 else "medium",
            "source": f"body:{body_type}({body_matches}matches)",
        }
    elif filename_type:
        return {
            "value": filename_type,
            "confidence": "medium",
            "source": f"filename:{filename_type}(unconfirmed)",
        }
    else:
        return {
            "value": "TENDER",
            "confidence": "low",
            "source": "default",
        }


def _merge_preliminary_table(metadata, table_results):
    """将前附表的键值对融合到 metadata 中。

    表格提取优先于 regex 提取（表格是更可靠的信息源）。
    """
    if not table_results:
        return metadata

    kv_pairs = {}

    # 来源1: 新分类器格式
    classification = table_results.get("_classification", {}) if isinstance(table_results, dict) else None
    if classification:
        prelim = classification.get("preliminary", {})
        if prelim and prelim.get("kv_pairs"):
            kv_pairs = prelim["kv_pairs"]

    # 来源2: 旧格式
    prelim_old = table_results.get("preliminary", {})
    if prelim_old and isinstance(prelim_old, dict):
        for k, v in prelim_old.items():
            if k not in kv_pairs and not k.startswith("table_"):
                kv_pairs[k] = v

    if not kv_pairs:
        return metadata

    # 键值对 → metadata 映射
    PRELIMINARY_TO_METADATA = {
        "评标办法": ("evaluation_method", "value"),
        "项目预算": ("budget", "total"),
        "采购预算": ("budget", "total"),
        "预算金额": ("budget", "total"),
        "联合体投标": ("allow_consortium", "value"),
        "是否允许联合体": ("allow_consortium", "value"),
        "投标保证金": ("bid_security_required", "value"),
        "履约保证金": ("performance_security_pct", "value"),
        "代理服务费": ("extra", "agency_fee"),
    }

    for key_text, value_text in kv_pairs.items():
        key_clean = str(key_text).strip().replace("\u2003", "").replace("\u3000", "").strip()
        value_clean = str(value_text).strip()

        # 查找映射
        mapped = None
        for table_key, meta_path in PRELIMINARY_TO_METADATA.items():
            if table_key in key_clean:
                mapped = meta_path
                break
            if key_clean in table_key:
                mapped = meta_path

        if not mapped:
            continue

        parser = "identity"
        if mapped == ("evaluation_method", "value"):
            pass
        elif mapped == ("budget", "total"):
            m = re.search(r"(\d+(?:\.\d+)?)", value_clean.replace(",", ""))
            if m:
                value_clean = float(m.group(1))
                if "万" in value_text:
                    value_clean *= 10000
            parser = "parse_money"
        elif mapped == ("allow_consortium", "value"):
            value_clean = value_clean in ("是", "允许", "可以", "接受")
        elif mapped == ("bid_security_required", "value"):
            value_clean = not (value_clean in ("免收", "无", "不收取", "0"))
        elif mapped == ("extra", "agency_fee"):
            m = re.search(r"(\d+)", value_clean)
            if m:
                value_clean = int(m.group(1))
            else:
                continue

        # 写入 metadata
        target = metadata
        parts = mapped[0].split(".")
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                target[part] = value_clean
            else:
                if part not in target:
                    target[part] = {}
                target = target[part]

    return metadata
