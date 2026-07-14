"""数据表分类引擎 — 仅识别三类数据表（技术规格、采购清单、评分），不碰资格性模板。

功能：
  1. 扫描所有表格，按表头模式识别三类数据表
  2. 提取结构化数据
  3. 与 format_requirements 独立（后者负责资格性模板提取）

使用方式：
  from app.infrastructure.table_classifier import classify_data_tables
  result = classify_data_tables(doc.tables)
  result["tech_requirements"]  → 技术规格表列表
  result["product_lists"]      → 采购清单表列表
  result["scoring"]            → 评分表数据
"""

import logging
import re

logger = logging.getLogger(__name__)


# ── 表格类型标识 ──
TYPE_TECH_REQUIREMENT = "TECH_REQUIREMENT"
TYPE_PRODUCT = "PRODUCT_LIST"
TYPE_SCORING = "SCORING"
TYPE_OTHER = "OTHER"


TECH_NAME_HEADERS = [
    "标的名称", "产品名称", "采购产品名称", "货物名称", "品名", "名称",
]
TECH_SPEC_HEADERS = [
    "规格型号及技术要求", "规格型号及技术参数", "技术参数与性能指标",
    "技术参数", "技术要求", "技术指标", "规格参数", "规格型号", "规格", "型号",
]
PRODUCT_PRICE_HEADERS = [
    "单价限价", "最高限价", "单价", "预算单价", "限价",
]
PRODUCT_QTY_HEADERS = [
    "预估数量", "数量", "需求量", "采购数量",
]
PRODUCT_UNIT_HEADERS = [
    "单位", "计量单位",
]
PRODUCT_REMARK_HEADERS = [
    "备注", "说明",
]
SCORING_NAME_HEADERS = ["评分因素", "评分项", "评分项目", "评审因素", "评审项目", "评审内容", "评分内容"]
SCORING_SCORE_HEADERS = ["分值", "分数", "权重", "标准分值", "标准分数", "权值"]
SCORING_CRITERIA_HEADERS = ["评分标准", "评审标准", "评审细则", "评分细则", "评审准则", "评标标准", "评分规则"]


def _normalize_header(text):
    """规范化表头文本，便于做语义匹配。"""
    normalized = str(text or "").strip()
    normalized = normalized.replace("\n", "").replace("\r", "")
    normalized = re.sub(r"[：:()（）\[\]【】\s]+", "", normalized)
    normalized = normalized.replace("★", "").replace("▲", "")
    return normalized


def _find_col_index(headers, candidates):
    """按候选语义在表头中寻找列索引。"""
    normalized_headers = [_normalize_header(h) for h in headers]
    normalized_candidates = [_normalize_header(c) for c in candidates if str(c or "").strip()]

    for i, header in enumerate(normalized_headers):
        for candidate in normalized_candidates:
            if header == candidate:
                return i

    for i, header in enumerate(normalized_headers):
        for candidate in normalized_candidates:
            if candidate and candidate in header:
                return i

    return None


def _safe_full_headers(table):
    """提取完整表头，兼容 python-docx Table 和 TableStub。"""
    if hasattr(table, 'headers') and table.headers:
        return [str(h).strip() for h in table.headers]
    if hasattr(table, 'rows') and table.rows:
        first_row = table.rows[0]
        if hasattr(first_row, 'cells'):
            return [cell.text.strip() for cell in first_row.cells]
    return []


def _safe_row_cells(row):
    """兼容两种格式提取行单元格值。"""
    if hasattr(row, 'cells'):
        return [cell.text.strip() for cell in row.cells]
    elif isinstance(row, (list, tuple)):
        return [str(c).strip() for c in row]
    return []


def _all_rows(table):
    """提取所有行（含表头），兼容两种格式。"""
    rows = []
    if hasattr(table, 'rows'):
        for row in table.rows:
            rows.append(_safe_row_cells(row))
    return rows


def _classify_table(headers):
    """根据表头判断表格类型。

    规则：
      - 技术规格表: 存在名称列 + 技术/规格列
      - 采购清单表: 存在名称列 + 价格/数量/单位等商务列
      - 评分表: 存在评分因素列 + 分值列 + 评分标准列
    """
    name_idx = _find_col_index(headers, TECH_NAME_HEADERS)
    spec_idx = _find_col_index(headers, TECH_SPEC_HEADERS)
    price_idx = _find_col_index(headers, PRODUCT_PRICE_HEADERS)
    qty_idx = _find_col_index(headers, PRODUCT_QTY_HEADERS)
    unit_idx = _find_col_index(headers, PRODUCT_UNIT_HEADERS)
    scoring_name_idx = _find_col_index(headers, SCORING_NAME_HEADERS)
    scoring_score_idx = _find_col_index(headers, SCORING_SCORE_HEADERS)
    scoring_criteria_idx = _find_col_index(headers, SCORING_CRITERIA_HEADERS)

    if name_idx is not None and spec_idx is not None:
        return TYPE_TECH_REQUIREMENT

    if name_idx is not None and any(idx is not None for idx in (price_idx, qty_idx, unit_idx)):
        return TYPE_PRODUCT

    if scoring_name_idx is not None and scoring_score_idx is not None and scoring_criteria_idx is not None:
        return TYPE_SCORING

    return TYPE_OTHER


def _extract_tech_requirement(rows):
    """从技术规格表提取数据。按表头语义抽取名称列和技术列。"""
    items = []
    headers = rows[0] if rows else []
    name_idx = _find_col_index(headers, TECH_NAME_HEADERS)
    spec_idx = _find_col_index(headers, TECH_SPEC_HEADERS)
    if name_idx is None and len(headers) > 1:
        name_idx = 1
    if spec_idx is None and len(headers) > 2:
        spec_idx = 2
    for row in rows[1:]:  # 跳过表头
        if not row:
            continue
        name = row[name_idx].strip() if name_idx is not None and name_idx < len(row) else ""
        spec = row[spec_idx].strip() if spec_idx is not None and spec_idx < len(row) else ""
        if name or spec:
            items.append({
                "name": name,
                "specification": spec,
            })
    return items


def _extract_product_list(rows):
    """从采购清单表提取数据。按表头语义抽取各列。"""
    items = []
    headers = rows[0] if rows else []
    name_idx = _find_col_index(headers, TECH_NAME_HEADERS)
    price_idx = _find_col_index(headers, PRODUCT_PRICE_HEADERS)
    qty_idx = _find_col_index(headers, PRODUCT_QTY_HEADERS)
    unit_idx = _find_col_index(headers, PRODUCT_UNIT_HEADERS)
    remark_idx = _find_col_index(headers, PRODUCT_REMARK_HEADERS)
    if name_idx is None and len(headers) > 1:
        name_idx = 1
    for row in rows[1:]:
        if not row:
            continue
        name = row[name_idx].strip() if name_idx is not None and name_idx < len(row) else ""
        if name and name != "统一下浮率" and name != "...":
            unit_price = row[price_idx].strip() if price_idx is not None and price_idx < len(row) else ""
            qty = row[qty_idx].strip() if qty_idx is not None and qty_idx < len(row) else ""
            unit = row[unit_idx].strip() if unit_idx is not None and unit_idx < len(row) else ""
            remark = row[remark_idx].strip() if remark_idx is not None and remark_idx < len(row) else ""
            items.append({
                "name": name,
                "unit_price": unit_price,
                "quantity": qty,
                "unit": unit,
                "remark": remark,
            })
    return items


def _extract_scoring(rows):
    """从评分表提取数据。格式：序号 | 评分因素 | 分值 | 评分标准"""
    dimensions = []
    for row in rows[1:]:
        if len(row) < 4:
            continue
        name = row[1].strip() if len(row) > 1 else ""
        score_str = row[2].strip() if len(row) > 2 else "0"
        criteria = row[3].strip() if len(row) > 3 else ""
        if name and name not in ("结论", "合计", "汇总", "总计", "总分"):
            # 从 "30分"、"30" 等提取数值
            score = 0
            score_clean = score_str.replace("分", "").strip()
            try:
                score = float(score_clean)
            except ValueError:
                pass
            dimensions.append({
                "name": name,
                "score": score,
                "criteria": criteria,
            })
    return {"method": "综合评分法", "total_score": sum(d.get("score", 0) for d in dimensions), "dimensions": dimensions}


def classify_data_tables(tables):
    """主入口：识别并提取三类数据表。

    Args:
        tables: list of python-docx Table or TableStub

    Returns:
        dict: {
            "tech_requirements": [{"name": ..., "specification": ...}, ...],
            "product_lists": [{"name": ..., "unit_price": ..., ...}, ...],
            "scoring": {"method": ..., "total_score": ..., "dimensions": [...]},
            "table_index": {1: "TECH_REQUIREMENT", 2: "PRODUCT_LIST", ...}
        }
    """
    result = {
        "tech_requirements": [],
        "product_lists": [],
        "scoring": {"method": "", "total_score": 0, "dimensions": []},
        "table_index": {},
    }

    for i, table in enumerate(tables):
        if not hasattr(table, 'rows') or not table.rows:
            continue

        headers = _safe_full_headers(table)
        if not headers:
            continue

        table_type = _classify_table(headers)
        table_no = i + 1
        result["table_index"][table_no] = table_type

        if table_type == TYPE_OTHER:
            continue

        rows = _all_rows(table)
        # ContentBlock 表格的 headers 和 rows 分开存储，_all_rows 只返回数据行
        # 但 _extract_* 函数期望 rows[0] 是表头行，需要将 headers 合并到 rows 开头
        if headers and hasattr(table, "headers") and getattr(table, "headers", None) is not None:
            if not rows or rows[0] != headers:
                rows.insert(0, headers)
        if not rows or len(rows) < 2:
            continue

        try:
            if table_type == TYPE_TECH_REQUIREMENT:
                items = _extract_tech_requirement(rows)
                if items:
                    result["tech_requirements"].append({
                        "table_no": table_no,
                        "headers": headers,
                        "items": items,
                    })

            elif table_type == TYPE_PRODUCT:
                items = _extract_product_list(rows)
                if items:
                    result["product_lists"].append({
                        "table_no": table_no,
                        "headers": headers,
                        "items": items,
                    })

            elif table_type == TYPE_SCORING:
                scoring_data = _extract_scoring(rows)
                if scoring_data.get("dimensions"):
                    result["scoring"] = scoring_data

        except Exception as exc:
            logger.warning("[table_classifier] 提取异常(table=%d, type=%s): %s", table_no, table_type, exc)

    logger.info(
        "[table_classifier] 数据表分类完成: total=%d, tech=%d, product=%d, scoring=%s",
        len(tables),
        len(result["tech_requirements"]),
        len(result["product_lists"]),
        "yes" if result["scoring"].get("dimensions") else "no",
    )

    return result
