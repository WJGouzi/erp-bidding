"""analysis_data v3 JSON schema 定义和组装逻辑。"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def preprocess_json(text: str) -> str:
    """统一清洗 JSON 字符串：去除 trailing comma、控制字符、BOM 等。
    
    用于所有可能从 LLM 或外部来源获取 JSON 的场景。
    """
    if not text:
        return text
    text = text.strip()
    # 去掉 BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    # 去掉 markdown 代码块标记
    if text.startswith("```"):
        idx = text.find("\n")
        if idx > 0:
            text = text[idx + 1:]
        else:
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
    # 找到第一个 { 和最后一个 }
    bs = text.find("{")
    be = text.rfind("}")
    if bs >= 0 and be > bs:
        text = text[bs:be + 1]
    # 去除控制字符（保留换行 \\n 和 tab \\t）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # 去除 trailing comma 在 } 和 ] 前
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text


# Phase 1 schema
NULL_METADATA = {
    "project_name": {"value": ""},
    "project_code": {"value": ""},
    "purchaser": {"name": "", "alias": "", "contact": ""},
    "agent": {"name": "", "contact": ""},
    "budget": {"total": 0, "note": "", "packages": {}},
    "key_dates": {
        "bid_deadline": "", "bid_opening": "",
        "bid_validity_days": "",
        "file_purchase_start": "", "file_purchase_end": "",
    },
    "bid_type": "",
    "evaluation_method": {"value": ""},
    "extra": {
        "file_purchase_price": "",
        "bid_submission_location": "",
        "special_declaration": "",
        "agency_fee": "",
        "winner_count_text": "",
        "acceptance_standard": "",
        "pricing_rule": "",
        "submission_copies": "",
        "service_period": "",
        "delivery_location": "",
        "payment_terms": "",
        "warranty_period": "",
        "submission_docs_summary": "",
        "submission_copy_detail": "",
        "pkg_special_qual": "",
    },
    "allow_consortium": False,
    "allow_subcontracting": False,
    "bid_security_required": False,
    "performance_security_pct": "",
    "package_count": 0,
    "document_type": {"value": "TENDER", "confidence": "low", "source": "default"},
    "tables": {},
}

# Phase 2 schema
NULL_ELIGIBILITY = {
    "summary": {"total_items": 0, "passed": 0, "attention_required": 0, "failed": 0},
    "qualifications": [],
    "disqualifications": [],
    "starred_requirements": [],
}

# Phase 3 schema
NULL_SCORING = {
    "method": "",
    "total_score": "",
    "dimensions": [],
}

# Phase 3 packages schema
NULL_PACKAGES = []

# Phase 4 schema
NULL_STRATEGY = {
    "package_priorities": [],
    "writing_focus": [],
    "cross_package": {},
}



def assemble_v3_analysis_data(
    metadata=None,
    eligibility=None,
    scoring=None,
    packages=None,
    strategy=None,
    section_scoring_map=None,
    pipeline_status="completed",
):
    """组装完整的 analysis_data v3 JSON 结构。"""
    result = {
        "version": "v3",
        "pipeline_status": pipeline_status,
        "metadata": metadata or dict(NULL_METADATA),
        "eligibility": eligibility or dict(NULL_ELIGIBILITY),
        "scoring": scoring or dict(NULL_SCORING),
        "packages": packages or list(NULL_PACKAGES),
        "strategy": strategy or dict(NULL_STRATEGY),
        "has_package": bool(packages and len(packages) > 0),
        "package_count": len(packages) if packages else 0,
    }


    dims = (result.get("scoring") or {}).get("dimensions", [])
    if dims:
        result["section_scoring_map"] = [
            {
                "section": d["name"],
                "max_score": d["score"],
                "type": d.get("type", "unknown"),
            }
            for d in dims
        ]
    elif section_scoring_map:
        result["section_scoring_map"] = section_scoring_map
    else:
        result["section_scoring_map"] = []

    return result


def analysis_data_to_json(data):
    """序列化 analysis_data 为 JSON 字符串，保证中文不乱码。"""
    return json.dumps(data, ensure_ascii=False, indent=2)
