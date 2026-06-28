# 表格矩阵分类

## 目标
新增 `table_classifier.py` 模块，在所有 Phase 之前对所有表格做矩阵分类，识别其类型并提取结构化数据。分类结果供三个 Phase 使用。

## 影响文件
- `CREATE app/infrastructure/table_classifier.py` — 通用表格分类引擎
- `MODIFY app/service_modules/task_pipeline/analysis_v3/__init__.py` — 在 Phase 1 前调用分类
- `MODIFY app/service_modules/task_pipeline/analysis_v3/phase1_metadata.py` — 融合前附表结果
- `MODIFY app/service_modules/task_pipeline/analysis_v3/phase3_scoring.py` — 融合产品清单结果

## 设计原则

### 分类规则不依赖任何特定标书

表头关键词选择**仅依据政府采购规范中的标准化列名**，不从具体文档推导：

| 表格类型 | 关键词依据 |
|---------|-----------|
| 须知前附表 | 《政府采购货物和服务招标投标管理办法》规定的格式 |
| 政府采购产品清单 | "四川政府采购一体化平台"标准化表头 |
| 评分表 | 《政府采购评审办法》规定的评审要素格式 |
| 响应应答表 | 招标文件中的标准化"招标要求→投标应答"格式 |

## 改动内容

### 1. 新增 `app/infrastructure/table_classifier.py`

```python
"""标书表格分类引擎。

基于表头关键词模式识别表格类型，不依赖正文内容、不依赖文档类型。
"""

# 表头关键词规则
TABLE_CLASSIFIER_RULES = {
    # 须知前附表（几乎所有政府采购标书必有）
    "PRELIMINARY": {
        "mandatory": ["说明"],
        "optional": ["应知事项", "条款名称", "须知事项", "内  容",
                      "说明和要求", "说明与要求"],
        "min_mandatory": 1,
        "min_optional": 1,
    },
    # 政府采购标准产品清单（四川一体化平台格式）
    "GOV_PRODUCT_LIST": {
        "mandatory": ["标的名称"],
        "optional": ["采购品目名称", "标的金额", "所属行业",
                      "核心产品", "进口产品", "节能产品"],
        "min_mandatory": 1,
        "min_optional": 2,
    },
    # 通用产品/报价清单
    "PRODUCT_LIST": {
        "mandatory": [],
        "optional": ["产品名称", "品名", "标的名称",
                      "规格型号", "规格", "型号",
                      "数量", "单位", "单价", "总价",
                      "计量单位", "最高限价"],
        "min_mandatory": 0,
        "min_optional": 3,
    },
    # 评分/评审表
    "SCORING": {
        "mandatory": [],
        "optional": ["评分因素", "评审因素",
                      "分值", "分数", "权重", "权值",
                      "评分标准", "评审标准", "评分细则",
                      "评分因素及权重"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
    # 招标要求响应表
    "RESPONSE_FORM": {
        "mandatory": [],
        "optional": ["招标要求", "投标应答",
                      "比选要求", "响应内容",
                      "采购项目要求", "响应应答", "响应情况"],
        "min_mandatory": 0,
        "min_optional": 2,
    },
}
```

### 2. 分类函数

```python
def classify_table(table):
    """对单张表格做类型分类。
    
    返回: (table_type: str, confidence: float)
    """
    if not table.rows:
        return ("EMPTY", 0.0)
    
    headers = [cell.text.strip()[:15] for cell in table.rows[0].cells]
    header_set = set(h.lower() for h in headers)
    
    scores = {}
    for type_name, rules in TABLE_CLASSIFIER_RULES.items():
        mandatory_hits = sum(1 for kw in rules["mandatory"] 
                             if any(kw in h for h in header_set))
        optional_hits = sum(1 for kw in rules["optional"] 
                            if any(kw in h for h in header_set))
        
        if mandatory_hits >= rules["min_mandatory"] and optional_hits >= rules["min_optional"]:
            confidence = (mandatory_hits + optional_hits) / max(len(rules["mandatory"]) + len(rules["optional"]), 1)
            scores[type_name] = min(confidence * 1.5, 1.0)  # 置信度加权
    
    if not scores:
        return ("OTHER", 0.0)
    
    best = max(scores, key=scores.get)
    return (best, scores[best])


def classify_all_tables(tables, min_confidence=0.3):
    """对所有表格分类，输出结构化的分类结果。"""
    result = {
        "preliminary": [],
        "product_lists": [],
        "scoring": [],
        "response_forms": [],
        "other_tables": [],
    }
    
    for i, table in enumerate(tables):
        table_type, confidence = classify_table(table)
        if confidence < min_confidence:
            result["other_tables"].append({"table_no": i+1, "type": table_type})
            continue
        
        table_data = _extract_table_data(table, table_type)
        if table_type == "PRELIMINARY":
            result["preliminary"] = table_data
        elif table_type in ("GOV_PRODUCT_LIST", "PRODUCT_LIST"):
            result["product_lists"].append(table_data)
        elif table_type == "SCORING":
            result["scoring"] = table_data
        elif table_type == "RESPONSE_FORM":
            result["response_forms"].append(table_data)
    
    return result


def _extract_table_data(table, table_type):
    """按类型提取表格结构化数据。"""
    if not table.rows:
        return {}
    
    headers = [cell.text.strip() for cell in table.rows[0].cells]
    rows_data = []
    for row in table.rows[1:]:
        cells = [cell.text.strip() for cell in row.cells]
        rows_data.append(dict(zip(headers, cells + [""] * (len(headers) - len(cells)))))
    
    if table_type == "PRELIMINARY":
        # 提取键值对
        kv_pairs = {}
        for row in rows_data:
            values = list(row.values())
            if len(values) >= 3:
                kv_pairs[values[1]] = values[2]  # 第二列=key, 第三列=value
            elif len(values) == 2:
                kv_pairs[values[0]] = values[1]
        return {"kv_pairs": kv_pairs, "raw_rows": rows_data}
    
    return {"headers": headers, "items": rows_data}
```

### 3. 集成到管线

```python
# start_analyze_v3() 中，第0步之后，Phase 1 之前：
table_results = classify_all_tables(doc.tables)
source_texts["table_results"] = table_results

# Phase 1: 前附表融合到 metadata
metadata = extract_metadata(doc_text, file_name, table_results=table_results)
# extract_metadata 内部会调用 _merge_preliminary(metadata, table_results["preliminary"])

# Phase 3: 产品清单融合到 packages
# extract_packages 会读取 table_results["product_lists"]
```

## Pre-PRELIMINARY 到 metadata 的映射规则

```python
PRELIMINARY_KEY_MAP = {
    # 评标方法
    "评标办法": ("evaluation_method", "value"),
    "评审方法": ("evaluation_method", "value"),
    "比选方法": ("evaluation_method", "value"),
    
    # 预算
    "项目预算": ("budget.note", "text"),
    "采购预算": ("budget.total", "parse_int"),
    "预算金额": ("budget.total", "parse_int"),
    
    # 联合体
    "联合体投标": ("allow_consortium", "parse_bool"),
    "是否允许联合体": ("allow_consortium", "parse_bool"),
    
    # 保证金
    "投标保证金": ("bid_security_required", "parse_bool"),
    "投标担保": ("bid_security_required", "parse_bool"),
    "履约保证金": ("performance_security_pct", "parse_pct"),
    
    # 有效期
    "投标有效期": ("key_dates.bid_validity_days", "parse_int"),
    
    # 分包
    "是否分包": ("package_count", "parse_package"),
    "分包": ("package_count", "parse_package"),
    
    # 代理费
    "采购代理服务费": ("extra.agency_fee", "parse_int"),
    "代理服务费": ("extra.agency_fee", "parse_int"),
    "招标代理服务费": ("extra.agency_fee", "parse_int"),
    
    # 其他
    "报价方式": ("extra.pricing_rule", "value"),
    "现场踏勘": ("extra.site_visit", "parse_bool"),
    "是否允许进口产品": ("extra.allow_import", "parse_bool"),
}
```

## 验收标准
1. 须知前附表（含"应知事项""条款名称""须知事项"等列）能被正确识别为 PRELIMINARY
2. 产品清单表（含"品名""标的名称""规格型号"等列）能被正确识别为 PRODUCT_LIST
3. 评分表（含"评分因素""分值""评分标准"等列）能被正确识别为 SCORING
4. 合同模板/其他无关表格被分类为 OTHER
5. 分类结果不影响当前 Phase 1/2/3 的输出结构（仅补充新数据源）
6. 对于不含任何标准表格的文档（如纯合同文本），分类器返回空结果，不中断管线

## 不包含
- 不改动现有 Phase 的 standalone 可用性（各 Phase 仍可独立调用）
- 不处理图片/扫描件中的表格
- 不改变 `analysis_data` 的顶层结构（仅扩充子字段）
