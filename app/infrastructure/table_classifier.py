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
try:
    from app.infrastructure.table_codec import to_per_cell as _codec_to_per_cell, to_dict as _codec_to_dict
except ImportError:
    _codec_to_per_cell = None
    _codec_to_dict = None

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


def _extract_raw_table(table) -> dict:
    """提取表格的原始行列数据（不依赖分类），含合并单元格信息。
    
    返回:
        {"headers": [...], "rows": [[...], ...], "merges": [...], "column_widths": [...]}
        merges: [{"type": "horizontal"|"vertical", "row": int, "col": int, "span": int}, ...]
        column_widths: 每列宽度（twips），空列表表示未提取到
    """
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    if not hasattr(table, 'rows') or not table.rows:
        return {"headers": [], "rows": [], "merges": [], "column_widths": []}
    
    # 提取所有行的单元格数据（兼容 python-docx Table 和 TableStub）
    first_row = table.rows[0]
    if hasattr(first_row, 'cells'):
        # python-docx Table: rows[0] 是 Row 对象
        headers = [cell.text.strip() for cell in first_row.cells]
    else:
        # TableStub: rows[0] 是 list[str]
        headers = [str(c).strip() for c in first_row]
    rows = []
    # 优先从 TableStub 的 merge_cells 属性读取（缓存重建时需保留原始合并信息）
    if hasattr(table, 'merge_cells') and table.merge_cells:
        merges = list(table.merge_cells)
    else:
        merges = []
    # 追踪每列已有的垂直合并范围，用于去重
    _covered_vmerge_ranges = {}  # col -> [(start_row, span), ...]
    # 记录每行已被水平合并覆盖的列
    covered_horiz = {}  # row_idx -> set(col_idx)

    for r_idx, row in enumerate(table.rows):
        if hasattr(row, 'cells'):
            # python-docx Row
            cells = [cell.text.strip() for cell in row.cells]
        else:
            # TableStub list
            cells = [str(c).strip() for c in row]
        covered = covered_horiz.setdefault(r_idx, set())
        for c_idx in range(len(cells)):
            cell_text = cells[c_idx]
            # 跳过已被合并覆盖的虚拟列
            if c_idx in covered:
                continue
            # 合并单元格检测（仅 python-docx Row 支持）
            if hasattr(row, 'cells') and c_idx < len(row.cells):
                cell = row.cells[c_idx]
                try:
                    tc = cell._tc
                    grid_span = tc.find(f'.//{ns}gridSpan')
                    v_merge = tc.find(f'.//{ns}vMerge')
                    if grid_span is not None:
                        span_val = int(grid_span.get(f'{ns}val', '1'))
                        if span_val > 1:
                            merges.append({"type": "horizontal", "row": r_idx, "col": c_idx, "span": span_val})
                            for cc in range(c_idx, min(c_idx + span_val, len(cells))):
                                covered.add(cc)
                    if v_merge is not None:
                        v_val = v_merge.get(f'{ns}val', 'continue')
                        if v_val == 'restart' or v_val is None:
                            merge_count = 1
                            for nr in range(r_idx + 1, len(table.rows)):
                                if hasattr(table.rows[nr], 'cells') and c_idx < len(table.rows[nr].cells):
                                    nc = table.rows[nr].cells[c_idx]
                                    nv = nc._tc.find(f'.//{ns}vMerge')
                                    if nv is not None and nv.get(f'{ns}val', 'continue') == 'continue':
                                        merge_count += 1
                                    elif nv is not None and nv.get(f'{ns}val', 'restart') == 'restart' and nc.text.strip() == cell_text:
                                        merge_count += 1
                                    else:
                                        break
                            if merge_count > 1:
                                # 去重：检查同一列是否已有垂直合并覆盖当前行的范围
                                _already_covered = False
                                for (_vr, _vspan) in _covered_vmerge_ranges.get(c_idx, []):
                                    if _vr <= r_idx < _vr + _vspan:
                                        _already_covered = True
                                        break
                                if not _already_covered:
                                    merges.append({"type": "vertical", "row": r_idx, "col": c_idx, "span": merge_count})
                                    _covered_vmerge_ranges.setdefault(c_idx, []).append((r_idx, merge_count))
                except Exception:
                    pass
        rows.append(cells)
    
    # 提取列宽（从 tblGrid/gridCol）
    column_widths = []
    try:
        tbl = getattr(table, '_tbl', None)
        if tbl is not None:
            ns_short = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            tbl_grid = tbl.find(f'.//{ns_short}tblGrid')
            if tbl_grid is not None:
                for gc in tbl_grid.findall(f'{ns_short}gridCol'):
                    w = gc.get(f'{ns_short}w')
                    if w:
                        column_widths.append(int(w))
        else:
            # TableStub 回退：从对象属性直接读取
            stored_cw = getattr(table, 'column_widths', None) or []
            if stored_cw:
                column_widths = list(stored_cw)
            # 从 TableStub 读取合并单元格信息
            stored_merges = getattr(table, 'merge_cells', None) or []
            if stored_merges and not merges:
                merges = list(stored_merges)
    except Exception:
        # 最后回退到 table.columns 方式
        try:
            for col in table.columns:
                column_widths.append(col.width)
        except Exception:
            pass
    
    # 生成 per-cell 格式（方便前端直接渲染）
    per_cell_data = {}
    if _codec_to_per_cell is not None and _codec_to_dict is not None:
        try:
            td = _codec_to_per_cell(headers, rows, merges, column_widths)
            per_cell_data = _codec_to_dict(td)
        except Exception:
            pass
    
    
    # ===== WPS 伪合并检测：相邻单元格文字相同但无 OOXML gridSpan/vMerge =====
    # 仅在 XML 检测未发现任何合并时执行，避免重复
    if not merges:
        # 水平伪合并：逐行检测连续相同非空文本
        all_rows_data = [headers] + rows
        for r_idx in range(len(all_rows_data)):
            c = 0
            while c < len(all_rows_data[r_idx]):
                cell_text = all_rows_data[r_idx][c].strip()
                if not cell_text:
                    c += 1
                    continue
                span = 1
                while c + span < len(all_rows_data[r_idx]) and all_rows_data[r_idx][c + span].strip() == cell_text:
                    span += 1
                if span > 1:
                    merges.append({"type": "horizontal", "row": r_idx, "col": c, "span": span})
                    c += span
                else:
                    c += 1
        
        # 垂直伪合并：逐列检测连续相同非空文本
        if merges:  # 只在有水平合并时才检测垂直（避免噪声）
            all_rows_data = [headers] + rows
            max_cols = max(len(r) for r in all_rows_data) if all_rows_data else 0
            for c_idx in range(max_cols):
                r = 0
                while r < len(all_rows_data):
                    cell_text = all_rows_data[r][c_idx].strip() if c_idx < len(all_rows_data[r]) else ""
                    if not cell_text:
                        r += 1
                        continue
                    v_span = 1
                    while r + v_span < len(all_rows_data):
                        next_text = all_rows_data[r + v_span][c_idx].strip() if c_idx < len(all_rows_data[r + v_span]) else ""
                        if next_text == cell_text:
                            v_span += 1
                        else:
                            break
                    if v_span > 1:
                        merges.append({"type": "vertical", "row": r, "col": c_idx, "span": v_span})
                        r += v_span
                    else:
                        r += 1

    # ===== 合并检测结束 =====
        return {"headers": headers, "rows": rows, "merges": merges, "column_widths": column_widths, "per_cell": per_cell_data}


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
        "raw_tables": [],
    }
    
    # 前置提取：所有表格的原始数据（无论分类结果如何）
    for table in tables:
        # 跳过非 python-docx Table 对象（如已解析的 dict/list）
        if not hasattr(table, 'rows') or not table.rows:
            result["raw_tables"].append({"headers": [], "rows": [], "merges": []})
            continue
        try:
            result["raw_tables"].append(_extract_raw_table(table))
        except Exception as exc:
            logger.warning("[table_classifier] 提取原始表格数据异常: %s", exc)
            result["raw_tables"].append({"headers": [], "rows": [], "merges": []})
            continue
    
    for i, table in enumerate(tables):
        # 跳过非 python-docx Table 对象（如已解析的 dict/list）
        if not hasattr(table, 'rows') or not table.rows:
            continue
        try:
            table_no = i + 1
            table_type, confidence = classify_table(table)
            result["table_index"][table_no] = table_type
        except Exception as exc:
            logger.warning("[table_classifier] 分类异常(table=%d): %s", i + 1, exc)
            result["other_tables"].append(i + 1)
            continue
        
        if confidence < min_confidence or table_type == TYPE_OTHER:
            result["other_tables"].append(table_no)
            continue
        
        try:
            data = _extract_table_data(table, table_type)
        except Exception as exc:
            logger.warning("[table_classifier] 提取表格数据异常(table=%d, type=%s): %s", table_no, table_type, exc)
            result["other_tables"].append(table_no)
            continue
        
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


def extract_table_surroundings(body, ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'):
    """提取文档中每个表格前后的段落文本。

    Args:
        body: python-docx Document.element.body
        ns: OOXML namespace

    Returns:
        list[dict]: [{"text_before": "...", "text_after": "..."}, ...]
        顺序与 doc.tables 对应
    """
    children = list(body)
    surroundings = []
    table_index = -1
    for i, child in enumerate(children):
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag != 'tbl':
            continue
        table_index += 1
        text_before = ""
        text_after = ""
        
        # 查找前一个非空段落
        for j in range(i - 1, max(i - 10, -1), -1):
            ptag = children[j].tag.split('}')[-1] if '}' in children[j].tag else children[j].tag
            if ptag == 'tbl':
                break  # 遇到上一个表格停止
            if ptag == 'p':
                ts = [t.text for t in children[j].findall(f'.//{ns}t') if t.text]
                txt = ''.join(ts).strip()
                if txt:
                    text_before = txt
                    break
        
        # 查找后一个非空段落
        for j in range(i + 1, min(i + 10, len(children))):
            ptag = children[j].tag.split('}')[-1] if '}' in children[j].tag else children[j].tag
            if ptag == 'tbl':
                break  # 遇到下一个表格停止
            if ptag == 'p':
                ts = [t.text for t in children[j].findall(f'.//{ns}t') if t.text]
                txt = ''.join(ts).strip()
                if txt:
                    text_after = txt
                    break
        
        surroundings.append({
            "table_index": table_index,
            "text_before": text_before,
            "text_after": text_after,
        })
    
    return surroundings
