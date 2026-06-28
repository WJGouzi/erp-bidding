"""标书表格分类引擎 — 基于表头关键词模式识别。

功能：
  1. 扫描所有表格，按表头模式分类（前附表/产品清单/评分表/响应表/其他）
  2. 按类型提取结构化数据
  3. 与现有 table_parser.py 互补（本模块专注于分类和简单提取）

不依赖：
  - 文档类型（TENDER/SELECTION 等）
  - 具体标书内容
  - 外部模型

使用方式：
  from app.infrastructure.table_classifier import classify_all_tables
  result = classify_all_tables(doc.tables)
  result["preliminary"]  → 前附表键值对
  result["product_lists"] → 产品清单列表
  result["scoring"]      → 评分表数据
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe_table_headers(table):
    """兼容 python-docx Table（原生）和 TableStub（来自缓存）两种格式提取表头。
    
    - python-docx Table: headers 在 rows[0], 通过 .cells 访问
    - TableStub (namedtuple): headers 在 .headers 属性, rows 是数据行
    """
    # TableStub 格式：headers 在独立属性
    if hasattr(table, 'headers') and table.headers:
        return [str(h)[:20] for h in table.headers]
    # python-docx Table 格式：headers 在 rows[0].cells
    if hasattr(table, 'rows') and table.rows:
        first_row = table.rows[0]
        if hasattr(first_row, 'cells'):
            return [cell.text.strip()[:20] for cell in first_row.cells]
    return []

def _safe_full_headers(table):
    """提取完整表头（不截断），兼容 python-docx Table 和 TableStub。"""
    if hasattr(table, 'headers') and table.headers:
        return [str(h) for h in table.headers]
    if hasattr(table, 'rows') and table.rows:
        first_row = table.rows[0]
        if hasattr(first_row, 'cells'):
            return [cell.text.strip() for cell in first_row.cells]
    return []


def _safe_row_cells(row):
    """兼容两种格式提取行单元格值。
    
    - python-docx Row: 通过 .cells 访问
    - list/tuple（TableStub 数据行）: 直接取值
    """
    if hasattr(row, 'cells'):
        return [cell.text.strip() for cell in row.cells]
    elif isinstance(row, (list, tuple)):
        return [str(c).strip() for c in row]
    return []

# 表格类型标识
TYPE_PRELIMINARY = "PRELIMINARY"
TYPE_GOV_PRODUCT = "GOV_PRODUCT_LIST"
TYPE_PRODUCT = "PRODUCT_LIST"
TYPE_SCORING = "SCORING"
TYPE_RESPONSE = "RESPONSE_FORM"
TYPE_TECH_REQUIREMENT = "TECH_REQUIREMENT"
TYPE_SERVICE_REQUIREMENT = "SERVICE_REQUIREMENT"
TYPE_BUSINESS_REQUIREMENT = "BUSINESS_REQUIREMENT"
TYPE_QUALIFICATION_CHECK = "QUALIFICATION_CHECK"
TYPE_OTHER = "OTHER"

# 表头关键词规则
# mandatory: 必须包含的关键词（至少 min_mandatory 个命中）
# optional: 可选关键词（至少 min_optional 个命中）
CLASSIFIER_RULES = {
    TYPE_PRELIMINARY: {
        "mandatory": ["说明"],
        "optional": ["应知事项", "条款名称", "须知事项", "内  容",
                      "说明和要求", "说明与要求", "要求"],
        "min_mandatory": 1,
        "min_optional": 1,
    },
    TYPE_GOV_PRODUCT: {
        "mandatory": ["标的名称"],
        "optional": ["采购品目名称", "标的金额", "所属行业",
                      "核心产品", "进口产品", "节能产品", "数量"],
        "min_mandatory": 1,
        "min_optional": 2,
    },
    TYPE_PRODUCT: {
        "mandatory": [],
        "optional": ["产品名称", "品名", "标的名称",
                      "规格型号", "规格", "型号",
                      "数量", "单位", "单价", "总价",
                      "计量单位", "最高限价", "采购产品名称"],
        "min_mandatory": 0,
        "min_optional": 3,
    },
    TYPE_SCORING: {
        "mandatory": [],
        "optional": ["评分因素", "评审因素",
                      "分值", "分数", "权重", "权值",
                      "评分标准", "评审标准", "评分细则",
                      "评分因素及权重",
                      "评审价格权重",
                      "具体标准和要求",
                      "关联响应文件"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
    TYPE_RESPONSE: {
        "mandatory": [],
        "optional": ["招标要求", "投标应答",
                      "比选要求", "响应内容",
                      "采购项目要求", "响应应答", "响应情况",
                      "磋商要求", "谈判要求"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
    # 技术参数要求表（政府采购一体化平台格式）
    TYPE_TECH_REQUIREMENT: {
        "mandatory": ["技术要求名称"],
        "optional": ["技术参数与性能指标", "符号标识", "技术参数"],
        "min_mandatory": 1,
        "min_optional": 0,
    },
    # 服务要求表（政府采购一体化平台格式）
    TYPE_SERVICE_REQUIREMENT: {
        "mandatory": [],
        "optional": ["服务要求名称", "服务要求内容"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
    # 商务要求表（政府采购一体化平台格式）
    TYPE_BUSINESS_REQUIREMENT: {
        "mandatory": [],
        "optional": ["商务要求名称", "商务要求内容"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
    # 资格审查表
    TYPE_QUALIFICATION_CHECK: {
        "mandatory": [],
        "optional": ["资格审查内容", "具体标准和要求", "关联投标文件格式文本",
                      "一般资格审查", "特定资格审查", "符合性审查"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
}


def classify_table(table) -> tuple:
    """对单张表格做类型分类。

    Args:
        table: python-docx Table 对象

    Returns:
        (table_type: str, confidence: float)
    """
    if not table.rows:
        return (TYPE_OTHER, 0.0)

    # 提取表头（兼容python-docx Table和TableStub）
    headers = [h.lower() for h in _safe_table_headers(table) if h]
    header_text = " ".join(headers)
    
    best_type = TYPE_OTHER
    best_score = 0.0
    
    for type_name, rules in CLASSIFIER_RULES.items():
        mandatory_hits = sum(1 for kw in rules["mandatory"] if kw.lower() in header_text)
        optional_hits = sum(1 for kw in rules["optional"] if kw.lower() in header_text)
        
        if mandatory_hits >= rules["min_mandatory"] and optional_hits >= rules["min_optional"]:
            # 置信度 = 命中数 / 总关键词数，加权
            # 置信度 = 命中数 / 最少需要命中数（避免被过多关键词稀释）
            need = rules["min_mandatory"] + rules["min_optional"]
            hit = mandatory_hits + optional_hits
            if need <= 0:
                need = 1
            score = hit / need
            # 加权：mandatory 命中权重更高
            if mandatory_hits > 0:
                score *= 1.2
            score = min(score, 1.0)
            
            if score > best_score:
                best_score = score
                best_type = type_name
    
    return (best_type, best_score)


def _extract_preliminary(table) -> dict:
    """从前附表提取键值对。
    
    标准格式：[序号, 应知事项/条款名称, 说明和要求]
    """
    if not table.rows:
        return {"kv_pairs": {}, "raw_rows": []}
    
    # 判断表格格式：TableStub（缓存）的 rows 不含表头，python-docx 的 rows[0] 是表头
    has_separate_headers = hasattr(table, 'headers') and bool(table.headers)
    rows_data = []
    # TableStub: rows 直接是数据行，不需要跳过第一行
    # python-docx Table: rows[0] 是表头，从 rows[1:] 开始取数据
    start_idx = 0 if has_separate_headers else 1
    for row in table.rows[start_idx:]:
        cells = _safe_row_cells(row)
        rows_data.append(cells)
    
    kv_pairs = {}
    for cells in rows_data:
        if len(cells) >= 3:
            key = cells[1]
            value = cells[2]
            if key:
                kv_pairs[key] = value
        elif len(cells) == 2:
            key = cells[0]
            value = cells[1]
            if key:
                kv_pairs[key] = value
    
    return {"kv_pairs": kv_pairs, "raw_rows": rows_data}


def _extract_product_list(table) -> dict:
    """从产品清单表提取结构化条目。"""
    if not table.rows:
        return {"headers": [], "items": []}
    
    # 提取完整表头（不截断）
    raw_headers = _safe_full_headers(table)
    # 判断表格格式：TableStub vs python-docx
    has_separate_headers = hasattr(table, 'headers') and bool(table.headers)
    start_idx = 0 if has_separate_headers else 1
    items = []
    for row in table.rows[start_idx:]:
        cells = _safe_row_cells(row)
        entry = {}
        for i, h in enumerate(raw_headers):
            entry[h] = cells[i] if i < len(cells) else ""
        items.append(entry)
    
    return {"headers": raw_headers, "items": items}


def _extract_scoring(table) -> dict:
    """从评分表提取评分维度。"""
    if not table.rows:
        return {"headers": [], "dimensions": []}
    
    raw_headers = _safe_full_headers(table)
    # 判断表格格式：TableStub vs python-docx
    has_separate_headers = hasattr(table, 'headers') and bool(table.headers)
    start_idx = 0 if has_separate_headers else 1
    dimensions = []
    for row in table.rows[start_idx:]:
        cells = _safe_row_cells(row)
        entry = {}
        for i, h in enumerate(raw_headers):
            entry[h] = cells[i] if i < len(cells) else ""
        dimensions.append(entry)
    
    return {"headers": raw_headers, "dimensions": dimensions}


def _extract_table_data(table, table_type: str) -> dict:
    """按类型提取表格结构化数据。"""
    if table_type == TYPE_PRELIMINARY:
        return _extract_preliminary(table)
    elif table_type in (TYPE_GOV_PRODUCT, TYPE_PRODUCT):
        return _extract_product_list(table)
    elif table_type == TYPE_SCORING:
        return _extract_scoring(table)
    elif table_type == TYPE_RESPONSE:
        return _extract_product_list(table)  # 响应表也按行列提取
    elif table_type in (TYPE_TECH_REQUIREMENT, TYPE_SERVICE_REQUIREMENT,
                        TYPE_BUSINESS_REQUIREMENT, TYPE_QUALIFICATION_CHECK,
                        TYPE_RESPONSE):
        return _extract_product_list(table)
    else:
        return {}


def classify_all_tables(tables, min_confidence: float = 0.25) -> dict:
    """对所有表格分类并提取结构化数据。

    Args:
        tables: python-docx Document.tables 列表
        min_confidence: 最小置信度阈值

    Returns:
        {
            "preliminary": {"kv_pairs": {...}, "raw_rows": [...]} 或 None,
            "product_lists": [...],
            "scoring": {...} 或 None,
            "response_forms": [...],
            "tech_requirements": [...],
            "service_requirements": [...],
            "business_requirements": [...],
            "qualification_checks": [...],
            "other_tables": [...],
            "table_index": {table_no: type_name}
        }
    """
    result = {
        "preliminary": None,
        "product_lists": [],
        "scoring": None,
        "response_forms": [],
        "tech_requirements": [],
        "service_requirements": [],
        "business_requirements": [],
        "qualification_checks": [],
        "other_tables": [],
        "table_index": {},
    }
    
    for i, table in enumerate(tables):
        table_no = i + 1
        table_type, confidence = classify_table(table)
        result["table_index"][table_no] = table_type
        
        if confidence < min_confidence or table_type == TYPE_OTHER:
            result["other_tables"].append(table_no)
            continue
        
        data = _extract_table_data(table, table_type)
        
        if table_type == TYPE_PRELIMINARY:
            result["preliminary"] = data
        elif table_type in (TYPE_GOV_PRODUCT, TYPE_PRODUCT):
            result["product_lists"].append(data)
        elif table_type == TYPE_SCORING:
            result["scoring"] = data
        elif table_type == TYPE_RESPONSE:
            result["response_forms"].append(data)
        elif table_type == TYPE_TECH_REQUIREMENT:
            result["tech_requirements"].append(data)
        elif table_type == TYPE_SERVICE_REQUIREMENT:
            result["service_requirements"].append(data)
        elif table_type == TYPE_BUSINESS_REQUIREMENT:
            result["business_requirements"].append(data)
        elif table_type == TYPE_QUALIFICATION_CHECK:
            result["qualification_checks"].append(data)
    
    logger.info(
        "[table_classifier] 分类完成: total=%d, preliminary=%s, "
        "product_lists=%d, scoring=%s, tech=%d, service=%d, biz=%d, qual=%d, other=%d",
        len(tables),
        "yes" if result["preliminary"] else "no",
        len(result["product_lists"]),
        "yes" if result["scoring"] else "no",
        len(result["tech_requirements"]),
        len(result["service_requirements"]),
        len(result["business_requirements"]),
        len(result["qualification_checks"]),
        len(result["other_tables"]),
    )
    
    return result
