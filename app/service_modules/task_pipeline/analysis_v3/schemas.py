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


def safe_json_loads(text: str, default=None, logger=logger) -> dict:
    """安全地解析 JSON，包含预处理和一键重试。"""
    if not text:
        return default or {}
    try:
        cleaned = preprocess_json(text)
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("[json] JSON 解析失败，尝试深度修复: %s", exc)
        # 更激进的修复：强制只保留 { } 之间的内容
        try:
            bs = text.find("{")
            be = text.rfind("}")
            if bs < 0 or be <= bs:
                return default or {}
            core = text[bs:be + 1]
            # 移除所有不可见字符
            core = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", core)
            # 修复 trailing comma
            core = re.sub(r",\s*}", "}", core)
            core = re.sub(r",\s*]", "]", core)
            # 修复不合法的控制字符
            core = re.sub(r"[\u0000-\u001f]", "", core)
            result = json.loads(core)
            return result
        except json.JSONDecodeError:
            logger.error("[json] 深度修复也失败")
            return default or {}


# Phase 1 schema
NULL_METADATA = {
    "project_name": {"value": ""},
    "project_code": {"value": ""},
    "purchaser": {"name": "", "alias": "", "contact": ""},
    "agent": {"name": "", "contact": ""},
    "budget": {"total": 0, "packages": {}},
    "key_dates": {
        "bid_deadline": "", "bid_opening": "",
        "bid_validity_days": 90,
        "file_purchase_start": "", "file_purchase_end": "",
    },
    "bid_type": "",
    "evaluation_method": "",
    "allow_consortium": False,
    "allow_subcontracting": False,
    "bid_security_required": False,
    "performance_security_pct": 0,
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
    "total_score": 0,
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


def _convert_table_classification_scoring(table_classification):
    """将 table_classification 检测到的评分表转换为 scoring 格式。"""
    scoring_tables = table_classification.get("scoring", [])
    if not scoring_tables:
        return None
    
    all_dims = []
    for table in scoring_tables:
        headers = table.get("headers", [])
        items = table.get("items", [])
        if not headers or not items:
            continue
        
        # 找评分因素、分值、评分标准对应的列
        name_col = None
        score_col = None
        criteria_col = None
        for i, h in enumerate(headers):
            hl = h.lower()
            if any(k in hl for k in ['评分因素', '评审因素', '考核内容', '评审内容']):
                name_col = i
            elif any(k in hl for k in ['分值', '分数', '得分']):
                score_col = i
            elif any(k in hl for k in ['评分标准', '具体标准', '评审标准']):
                criteria_col = i
        
        if name_col is None and score_col is None:
            # Try first two columns
            if len(headers) >= 2:
                name_col = 0
                score_col = 1
        
        for item in items:
            dim = {"name": "", "score": 0, "criteria": "", "type": None}
            if name_col is not None:
                val = item.get(headers[name_col]) if isinstance(item, dict) else (item[name_col] if isinstance(item, (list, tuple)) and len(item) > name_col else "")
                dim["name"] = str(val).strip() if val else ""
            if score_col is not None:
                val = item.get(headers[score_col]) if isinstance(item, dict) else (item[score_col] if isinstance(item, (list, tuple)) and len(item) > score_col else "")
                m = re.search(r"(\d+\.?\d*)", str(val))
                if m:
                    try:
                        dim["score"] = int(float(m.group(1)))
                    except ValueError:
                        pass
            if criteria_col is not None:
                val = item.get(headers[criteria_col]) if isinstance(item, dict) else (item[criteria_col] if isinstance(item, (list, tuple)) and len(item) > criteria_col else "")
                dim["criteria"] = str(val).strip() if val else ""
            
            # 兜底：从名称中提取分数（如"参与报价（20分）"）
            if dim["score"] == 0 and dim["name"]:
                m2 = re.search(r'[（(](\d+)\s*分[）)]', dim["name"])
                if m2:
                    try:
                        dim["score"] = int(m2.group(1))
                    except ValueError:
                        pass
            
            if dim["name"] or dim["score"] > 0:
                all_dims.append(dim)
    
    if not all_dims:
        return None
    
    total_score = sum(d["score"] for d in all_dims if d["score"])
    
    return {
        "method": "综合评分法",
        "total_score": total_score,
        "dimensions": all_dims,
    }


def assemble_v3_analysis_data(
    metadata=None,
    eligibility=None,
    scoring=None,
    packages=None,
    strategy=None,
    section_scoring_map=None,
    pipeline_status="completed",
    table_classification=None,
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
    if table_classification and isinstance(table_classification, dict):
        result["table_classification"] = table_classification
        # 如果规则没提取到评分维度，但 table_classification 检测到了评分表，则转换过来
        current_scoring = result.get("scoring", {})
        scoring_dimensions = current_scoring.get("dimensions") if isinstance(current_scoring, dict) else None
        if (not scoring_dimensions and 
            table_classification.get("scoring")):
            tc_scoring = _convert_table_classification_scoring(table_classification)
            if tc_scoring and tc_scoring.get("dimensions"):
                result["scoring"] = tc_scoring

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
