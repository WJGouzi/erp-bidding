"""评分标准模块：按商务/技术分组。"""
import json
import logging

logger = logging.getLogger(__name__)


def _classify_dimension(dim: dict) -> str:
    """判断评分维度属于商务还是技术。"""
    dim_type = (dim.get("type") or "").lower()
    name = (dim.get("name") or "").lower()

    # 技术类关键词
    tech_keywords = ["技术", "参数", "质量", "方案", "实施", "产品", "样品", "检测", "指标"]
    # 商务类关键词
    biz_keywords = ["商务", "业绩", "售后", "服务", "履约", "资质", "价格", "信誉"]

    if any(k in dim_type for k in ["tech", "技术"]):
        return "technical"
    if any(k in dim_type for k in ["biz", "商务", "price", "价格"]):
        return "business"

    # 按名称智能分类
    for kw in tech_keywords:
        if kw in name:
            return "technical"
    for kw in biz_keywords:
        if kw in name:
            return "business"

    return "technical"  # 默认归为技术


def assemble_scoring(result, analysis: dict) -> dict:
    """组装评分标准。"""
    scoring = analysis.get("scoring", {})
    if isinstance(scoring, str):
        try:
            scoring = json.loads(scoring)
        except (json.JSONDecodeError, TypeError):
            scoring = {}

    dimensions = scoring.get("dimensions", [])
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions)
        except (json.JSONDecodeError, TypeError):
            dimensions = []

    business_dims = []
    technical_dims = []

    for dim in dimensions:
        dim_name = (dim.get("name", "") or "").strip()
        # 过滤合计行、汇总行等非实际评分维度
        if any(kw in dim_name for kw in ["合计", "汇总", "总计", "总分"]):
            continue
        entry = {
            "name": dim_name,
            "score": dim.get("score", 0),
            "weight": dim.get("weight", dim.get("score", 0)),
            "criteria": dim.get("criteria", dim.get("standard", "")),
            "type": dim.get("type", "subjective"),
        }
        category = _classify_dimension(dim)
        if category == "business":
            business_dims.append(entry)
        else:
            technical_dims.append(entry)

    return {
        "method": scoring.get("method", ""),
        "total_score": scoring.get("total_score", 0),
        "price_weight": scoring.get("price_weight", 0),
        "business": business_dims,
        "technical": technical_dims,
    }
