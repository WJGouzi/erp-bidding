"""通用表格解析引擎 — 类型识别 + 按类型策略提取 + 结果融合。

架构：
  阶段1：类型识别（列数+表头关键词）
  阶段2：按类型策略提取（5种表格类型各有专属提取逻辑）
  阶段3：结果融合（表格优先于 regex）

支持的表格类型：
  - PRELIMINARY_TABLE: 前附表（序号|内容|说明与要求）
  - SCORING_TABLE: 评分表（评分因素|分值|评分标准）
  - PRODUCT_LIST: 产品清单（品名|规格|数量|品牌）
  - QUALIFICATION_TABLE: 资质表（供应商信息）
  - RESPONSE_FORMAT: 响应格式表
  - GENERIC_TABLE: 通用表格（仅 flatten 为文本）
"""

import enum
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TableType(str, enum.Enum):
    PRELIMINARY_TABLE = "PRELIMINARY_TABLE"
    SCORING_TABLE = "SCORING_TABLE"
    PRODUCT_LIST = "PRODUCT_LIST"
    QUALIFICATION_TABLE = "QUALIFICATION_TABLE"
    RESPONSE_FORMAT = "RESPONSE_FORMAT"
    GENERIC_TABLE = "GENERIC_TABLE"


# ──────────────────────────────────────────────
#  阶段1：表格类型识别
# ──────────────────────────────────────────────

# 每种类型的判定特征
TYPE_SIGNATURES = [
    # 前附表：3列，表头含"内容"和"说明与要求"
    (TableType.PRELIMINARY_TABLE, {
        "min_cols": 2, "max_cols": 4,
        "header_keywords": ["说明"],
        "header_any": ["序号", "内容", "说明与要求", "说明和要求",
                        "应知事项", "条款名称", "须知事项", "要求"],
    }),
    # 评分表：表头含评分相关关键词
    (TableType.SCORING_TABLE, {
        "min_cols": 2, "max_cols": 10,
        "header_keywords": ["评分", "分值", "分数", "得分", "权重", "评审"],
    }),
    # 产品清单：表头含产品/规格/数量相关关键词
    (TableType.PRODUCT_LIST, {
        "min_cols": 2,
        "header_keywords": ["品名", "名称", "品牌", "规格", "型号",
                            "数量", "单位", "单价", "产地", "试剂"],
    }),
    # 资质表：表头含供应商/注册信息
    (TableType.QUALIFICATION_TABLE, {
        "min_cols": 2,
        "header_keywords": ["供应商", "注册地址", "法定代表人",
                            "营业执照", "组织机构代码"],
    }),
    # 响应格式表：表头含"比选文件条目号"或"响应文件的应答"
    (TableType.RESPONSE_FORMAT, {
        "min_cols": 2,
        "header_keywords": ["比选文件", "招标文件", "条目号",
                            "响应文件的应答", "应答"],
    }),
]


def classify_table(
    headers: List[str],
    num_rows: int,
    num_cols: int,
    sample_cells: Optional[List[str]] = None,
) -> TableType:
    """基于表头+列数+内容特征判定表格类型。

    Args:
        headers: 表头行文本列表。
        num_rows: 总行数（含表头）。
        num_cols: 总列数。
        sample_cells: 少量单元格内容样本，用于辅助判定。

    Returns:
        判定的表格类型。
    """
    if num_cols < 2 or num_rows < 2:
        return TableType.GENERIC_TABLE

    header_text = "".join(h.replace(" ", "").replace("\u3000", "").replace("\t", "") for h in headers if h.strip())
    sample_text = "".join(c.replace(" ", "").replace("\u3000", "").replace("\t", "") for c in (sample_cells or [])) + header_text

    best_match = TableType.GENERIC_TABLE
    best_score = 0

    for table_type, sig in TYPE_SIGNATURES:
        score = 0

        # 列数范围检查
        min_c = sig.get("min_cols", 0)
        max_c = sig.get("max_cols", 999)
        if num_cols < min_c or num_cols > max_c:
            continue

        # 必须包含的关键词
        req_kws = sig.get("header_keywords", [])
        for kw in req_kws:
            if kw in header_text:
                score += 3
            elif kw in sample_text:
                score += 1

        # 可选包含关键词（加分项）
        any_kws = sig.get("header_any", [])
        for kw in any_kws:
            if kw in header_text:
                score += 1

        # 唯一性奖励：匹配数越多，类型判定的特异性越高
        if score > best_score:
            best_score = score
            best_match = table_type

    # 评分表如果表头明显含评分关键词，提高置信度
    if best_match == TableType.SCORING_TABLE and best_score >= 6:
        return best_match
    # 前附表必须满足列数2-4且表头含"说明"
    if best_match == TableType.PRELIMINARY_TABLE:
        if 2 <= num_cols <= 4 and "说明" in header_text:
            return best_match
        return TableType.GENERIC_TABLE

    return best_match if best_score >= 3 else TableType.GENERIC_TABLE


# ──────────────────────────────────────────────
#  阶段2：按类型策略提取
# ──────────────────────────────────────────────

def _coalesce_cells(row_cells: List[str]) -> str:
    """合并一行中的多个单元格文本。"""
    return " | ".join(c.strip() for c in row_cells if c.strip())


def extract_preliminary_table(rows: List[List[str]], headers: List[str]) -> Dict[str, Any]:
    """提取前附表（3列：序号|内容|说明与要求）的键值对。

    Args:
        rows: 数据行（不含表头）。
        headers: 表头行。

    Returns:
        Dict[str, Any]: {key: value, ...} 以及原始数据。
    """
    result = {}
    raw_rows = []

    # 找到"内容"和"说明与要求"列的索引
    content_idx = None
    value_idx = None
    for i, h in enumerate(headers):
        h_clean = h.strip()
        if "内容" in h_clean:
            content_idx = i
        elif "说明" in h_clean or "要求" in h_clean:
            value_idx = i

    if content_idx is None or value_idx is None:
        # 降级：取第1列和第2列
        content_idx, value_idx = 0, min(1, len(headers) - 1)
    # 如果还没找到，尝试去除空格后匹配
    if content_idx is None or value_idx is None:
        for i, h in enumerate(headers):
            h_clean = h.strip().replace(" ", "").replace("\u3000", "")
            if "\u5185\u5bb9" in h_clean:
                content_idx = i
            elif "\u8bf4\u660e" in h_clean or "\u8981\u6c42" in h_clean:
                value_idx = i

    for row_idx, row in enumerate(rows):
        if len(row) <= max(content_idx, value_idx):
            continue

        key = row[content_idx].strip()
        value = row[value_idx].strip()

        if not key and not value:
            continue

        # 如果 key 是纯数字（序号列而非内容列），修正索引
        if key.isdigit() and content_idx == 0 and value_idx == 1 and len(row) >= 3:
            key = row[1].strip()
            value = row[2].strip()
            content_idx, value_idx = 1, 2

        if not key:
            continue

        raw_rows.append({"row": row_idx + 1, "key": key, "value": value})

        # 特殊字段智能解析
        parsed = _parse_preliminary_value(key, value)
        if parsed is not None:
            result_key, result_value = parsed
            result[result_key] = result_value
        else:
            # 通用键值对存储
            result[key] = value

    result["_raw_rows"] = raw_rows
    return result


def _parse_preliminary_value(key: str, value: str) -> Optional[Tuple[str, Any]]:
    """智能解析前附表的值。"""
    if not key or not value:
        return None

    # 比选方法/评标方法
    if any(kw in key for kw in ["比选方法", "评标方法", "评审办法", "评审方法"]):
        for method in ["综合评分法", "最低评标价法", "综合评估法"]:
            if method in value:
                return ("evaluation_method", method)

    # 联合体
    if "联合体" in key:
        is_allowed = not any(kw in value for kw in ["不允许", "不接受", "不组织"])
        return ("allow_consortium", is_allowed)

    # 保证金
    if "保证金" in key:
        if any(kw in value for kw in ["不收取", "免收", "无"]):
            return ("bid_security_required", False)
        m = re.search(r"(\d+[,.]?\d*)\s*(万|元)", value)
        if m:
            amount = float(m.group(1).replace(",", ""))
            if m.group(2) == "万":
                amount *= 10000
            return ("bid_security_amount", amount)

    # 代理服务费
    if "代理服务费" in key or "代理费" in key:
        m = re.search(r"(\d+)\s*元", value)
        if m:
            return ("agency_fee", int(m.group(1)))
        # 中文数字
        cn_num = re.search(r"([零壹贰叁肆伍陆柒捌玖拾佰仟万]+)", value)
        if cn_num:
            num = _cn2int(cn_num.group(1))
            if num:
                return ("agency_fee", num)

    # 成交供应商数量
    if "成交" in key or "中标" in key or "供应商数量" in key:
        pkg_counts = {}
        # 匹配"采购包X：...Y家"模式
        for m in re.finditer(r"(?:采购)?包(\d+)[^。]*?(\d+)\s*家", value):
            pkg_counts[int(m.group(1))] = int(m.group(2))
        if pkg_counts:
            return ("winner_count", pkg_counts)
        # 单个数字
        m = re.search(r"(\d+)\s*家", value)
        if m:
            return ("winner_count", int(m.group(1)))

    # 履约要求/验收标准
    if "验收" in key:
        return ("acceptance_standard", value[:200])
    if "履约" in key:
        return ("performance_requirement", value[:200])

    return None


def _cn2int(cn_str: str) -> Optional[int]:
    """中文数字转整数。"""
    cn_map = {"零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4,
              "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
              "拾": 10, "佰": 100, "仟": 1000, "万": 10000}
    total = 0
    temp = 0
    for ch in cn_str:
        if ch in cn_map:
            val = cn_map[ch]
            if val >= 10:
                if temp == 0:
                    temp = 1
                total += temp * val
                temp = 0
            else:
                temp = val
    total += temp
    return total if total > 0 else None


def extract_product_list(rows: List[List[str]], headers: List[str]) -> Dict[str, Any]:
    """提取产品清单的结构化数据。

    Args:
        rows: 数据行。
        headers: 表头行。

    Returns:
        包含产品统计和结构化数据的字典。
    """
    # 建立表头到标准字段的映射
    header_map = _map_product_headers(headers)

    items = []
    categories = set()

    for row in rows:
        item = {}
        has_data = False
        for std_field, col_idx in header_map.items():
            if col_idx < len(row):
                val = row[col_idx].strip()
                if val:
                    item[std_field] = val
                    if std_field == "name":
                        has_data = True
                        # 尝试推断类别
                        cat = _infer_category(val)
                        if cat:
                            categories.add(cat)
        if has_data:
            items.append(item)

    return {
        "total_items": len(items),
        "categories": sorted(categories) if categories else [],
        "has_pricing": any("price" in item or "unit_price" in item for item in items),
        "items": items[:100],  # 只保留前100项
    }


_PRODUCT_HEADER_MAP = {
    "name": ["品名", "名称", "产品名称", "试剂名称", "货物名称", "商品名"],
    "spec": ["规格", "规格型号", "型号", "技术规格", "参数"],
    "brand": ["品牌", "生产厂家", "厂家", "制造商"],
    "qty": ["数量", "需求量", "预估数量", "采购量"],
    "unit": ["单位", "计量单位"],
    "unit_price": ["单价", "预算单价", "最高限价"],
    "total_price": ["总价", "金额", "合计"],
    "产地": ["产地", "来源"],
    "备注": ["备注", "说明"],
}


def _map_product_headers(headers: List[str]) -> Dict[str, int]:
    """将表头映射到标准字段名。"""
    mapping = {}
    for i, h in enumerate(headers):
        h_clean = h.strip()
        for std_field, candidates in _PRODUCT_HEADER_MAP.items():
            if any(c in h_clean for c in candidates):
                if std_field not in mapping:
                    mapping[std_field] = i
                break
    return mapping


def _infer_category(name: str) -> Optional[str]:
    """根据品名推断所属类别。"""
    cat_map = [
        (["标准", "标准物质", "标准溶液", "标液"], "标准物质/标准溶液"),
        (["试剂", "化学试剂", "分析纯", "AR"], "化学试剂"),
        (["培养基", "菌", "微生物"], "微生物/培养基"),
        (["气体", "氮气", "氩气", "氦气", "乙炔"], "气体"),
        (["玻璃", "烧杯", "量筒", "试管", "容量瓶"], "玻璃仪器"),
        (["滤", "膜", "针头"], "过滤耗材"),
        (["手套", "口罩", "防护", "移液", "枪头"], "实验耗材"),
    ]
    for keywords, category in cat_map:
        if any(kw in name for kw in keywords):
            return category
    return None


# ──────────────────────────────────────────────
#  阶段3：评分表增强
# ──────────────────────────────────────────────

def enhance_scoring_dimensions(
    dimensions: List[Dict],
    rows: List[List[str]],
    headers: List[str],
) -> List[Dict]:
    """增强评分维度：子维度检测、评分标准原文保留。

    用来增强已有的 parse_scoring_table 结果。
    """
    # 找评分项和评分标准列的索引
    name_idx = None
    criteria_idx = None
    for i, h in enumerate(headers):
        if any(kw in h for kw in ["评分因素", "评分项", "评审因素", "评审内容"]):
            name_idx = i
        if any(kw in h for kw in ["评分标准", "评审标准", "评审细则", "评分细则"]):
            criteria_idx = i

    if name_idx is None:
        return dimensions  # 无法增强

    for dim in dimensions:
        dim_name = dim.get("name", "")
        # 检查是否有子维度
        sub_dims = dim.get("sub_dimensions", [])

        # 在表格行中找该评分项的对应行
        for row in rows:
            if len(row) <= max(name_idx, criteria_idx or 0):
                continue
            row_name = row[name_idx].strip() if name_idx < len(row) else ""

            # 跳过空行或序号行
            if not row_name or row_name.isdigit():
                continue

            # 如果行名包含评分项名，则可能是子维度或评分标准
            if row_name in dim_name or dim_name in row_name:
                if criteria_idx is not None and criteria_idx < len(row):
                    criteria = row[criteria_idx].strip()
                    if criteria and len(criteria) > 10:
                        dim["scoring_standard"] = criteria
                        # 从评分标准提取子维度
                        extracted_subs = _extract_sub_dims_from_text(criteria)
                        if extracted_subs:
                            sub_dims.extend(extracted_subs)

        if sub_dims:
            dim["sub_dimensions"] = sub_dims

    return dimensions


def _extract_sub_dims_from_text(text: str) -> List[Dict]:
    """从评分标准文本中提取子维度。"""
    sub_dims = []

    # 编号列表：1.xxx 2.xxx
    nums = re.findall(r"(\d+)[、.．\s]\s*([^。\d]{4,60})", text)
    for num, desc in nums:
        desc = desc.strip()
        if len(desc) > 4:
            sub_dims.append({"name": desc[:60], "description": desc[:120]})

    # 中文编号：（一）xxx（二）xxx
    cns = re.findall(r"（[一二三四五六七八九十]+）\s*([^。]{4,60})", text)
    for desc in cns:
        desc = desc.strip()
        if len(desc) > 4:
            sub_dims.append({"name": desc[:60], "description": desc[:120]})

    return sub_dims


# ──────────────────────────────────────────────
#  对外入口
# ──────────────────────────────────────────────

def parse_all_tables(docx_tables) -> Dict[str, Any]:
    """遍历 docx 所有表格，分类+提取+返回结构化结果。

    Args:
        docx_tables: python-docx Document.tables 列表，
                     或者 ContentBlock type=table 的列表。

    Returns:
        {
            "preliminary": {key: value, ...},
            "scoring_tables": [{...}, ...],
            "product_lists": [{...}, ...],
            "generic_tables": [flattened_text, ...],
            "all_key_values": {merged key-value pairs},
        }
    """
    result = {
        "preliminary": {},
        "scoring_tables": [],
        "product_lists": [],
        "generic_tables": [],
        "all_key_values": {},
    }

    for table in docx_tables:
        # 统一接口：支持原生 DOCX table 和 ContentBlock/duck-typed table
        if hasattr(table, "rows") and hasattr(table, "headers"):
            # ContentBlock table (duck-typing: has both rows and headers attributes)
            headers = table.headers or []
            data_rows = table.rows if hasattr(table, "rows") else []
            num_rows = len(data_rows) + 1
            num_cols = max(len(headers), max((len(r) for r in data_rows), default=0))
            sample_cells = []
            for row in data_rows[:3]:
                for c in (row or [])[:3]:
                    if c:
                        sample_cells.append(str(c))
        elif hasattr(table, "rows"):
            # python-docx Table
            rows_obj = table.rows
            num_rows = len(rows_obj)
            num_cols = len(rows_obj[0].cells) if num_rows > 0 else 0
            headers = [cell.text.strip() for cell in rows_obj[0].cells] if num_rows > 0 else []
            data_rows = []
            sample_cells = []
            for r_idx, row in enumerate(rows_obj):
                cells = [cell.text.strip() for cell in row.cells]
                if r_idx == 0:
                    continue
                data_rows.append(cells)
                for c in cells[:3]:
                    if c:
                        sample_cells.append(c)
        else:
            continue

        table_type = classify_table(headers, num_rows, num_cols, sample_cells)

        if table_type == TableType.PRELIMINARY_TABLE:
            parsed = extract_preliminary_table(data_rows, headers)
            result["preliminary"] = parsed
            # 合并到 all_key_values
            for k, v in parsed.items():
                if not k.startswith("_"):
                    result["all_key_values"][k] = v

        elif table_type == TableType.SCORING_TABLE:
            scoring_info = {
                "headers": headers,
                "row_count": len(data_rows),
                "has_scoring_standard": any(
                    any(kw in h for kw in ["评分标准", "评审标准", "评审细则"])
                    for h in headers
                ),
            }
            result["scoring_tables"].append(scoring_info)

        elif table_type == TableType.PRODUCT_LIST:
            parsed = extract_product_list(data_rows, headers)
            result["product_lists"].append(parsed)

        else:
            # 通用/其他表格：flatten 为文本
            flat_lines = []
            if headers:
                flat_lines.append(" | ".join(headers))
            for row in data_rows[:50]:
                flat_lines.append(" | ".join(row))
            result["generic_tables"].append("\n".join(flat_lines))

    return result
