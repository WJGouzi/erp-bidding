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

logger = logging.getLogger(__name__)


# ── 表格类型标识 ──
TYPE_TECH_REQUIREMENT = "TECH_REQUIREMENT"
TYPE_PRODUCT = "PRODUCT_LIST"
TYPE_SCORING = "SCORING"
TYPE_OTHER = "OTHER"


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

    规则（基于实际 9 包 docx 表头特征）：
      - 技术规格表: 同时含"标的名称"+"规格型号"
      - 采购清单表: 同时含"标的名称"+"单价限价"（含★变体）
      - 评分表: 同时含"评分因素"+"分值"+"评分标准"
    """
    header_text = " ".join(headers)

    # 技术规格表: 必须同时含"标的名称" + "规格型号及技术要求"（完整短语）
    # 排除格式模板表（仅含"规格型号"不带"及技术要求"）
    if "标的名称" in header_text and "规格型号及技术要求" in header_text:
        return TYPE_TECH_REQUIREMENT

    # 采购清单表: "标的名称" + "单价限价"
    if "标的名称" in header_text:
        if "单价限价" in header_text:
            return TYPE_PRODUCT

    # 评分表: "评分因素" + "分值" + "评分标准"
    if "评分因素" in header_text and "分值" in header_text and "评分标准" in header_text:
        return TYPE_SCORING

    return TYPE_OTHER


def _extract_tech_requirement(rows):
    """从技术规格表提取数据。格式：序号 | 标的名称 | 规格型号及技术要求"""
    items = []
    for row in rows[1:]:  # 跳过表头
        if len(row) < 3:
            continue
        name = row[1].strip() if len(row) > 1 else ""
        spec = row[2].strip() if len(row) > 2 else ""
        if name:
            items.append({
                "name": name,
                "specification": spec,
            })
    return items


def _extract_product_list(rows):
    """从采购清单表提取数据。格式：序号 | 标的名称 | ★单价限价 | 预估数量 | 单位 | 备注"""
    items = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        name = row[1].strip() if len(row) > 1 else ""
        if name and name != "统一下浮率" and name != "...":
            unit_price = row[2].strip() if len(row) > 2 else ""
            qty = row[3].strip() if len(row) > 3 else ""
            unit = row[4].strip() if len(row) > 4 else ""
            remark = row[5].strip() if len(row) > 5 else ""
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
