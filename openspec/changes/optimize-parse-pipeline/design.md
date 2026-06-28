# 解析管线优化 — 设计文档（基于 10 份真实标书分析）

---

## 总体架构

```
                    start_analyze_v3(task, source_texts)
                              │
                 ┌────────────┴────────────┐
                 │  第0步：文档分类         │
                 │  classify_document()    │
                 │  → doc_type + bid_type  │
                 └────────────┬────────────┘
                              │
                 ┌────────────┴────────────┐
                 │  第1步：表格矩阵分类 ←── 新增核心模块
                 │  table_classifier.py   │
                 │  → 扫描所有表格          │
                 │  → 按表头模式分类        │
                 │  → 返回表格索引映射      │
                 └────────────┬────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
  Phase 1: metadata    Phase 2: eligibility   Phase 3: scoring
  集成前附表键值对      模板分层+章节定位      表格参数+评分
  集成产品清单提取                          |
        │                     │             集成产品清单
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                     _build_strategy_from_phases()
                              │
                              ▼
                        _complete_analysis()
                              │
                        to_dict() v3 修复
```

---

## 1. 表格矩阵分类模块（新建）

### 动机

10 份标书中有 222 张表，其中前附表、产品清单表、评分表占结构化的 70% 以上。但当前管线只在 Phase 3 处理了评分表，其他表格被 flatten 后丢失结构。

### 表头模式识别规则

基于 10 份标书验证的通用模式：

```python
TABLE_CLASSIFIER_RULES = {
    # 须知前附表
    "PRELIMINARY": {
        "mandatory": ["说明"],
        "optional": ["应知事项", "条款名称", "须知事项", "内  容"],
        "min_mandatory": 1,
        "min_optional": 1,
    },
    # 政府采购标准产品清单
    "GOV_PRODUCT_LIST": {
        "mandatory": ["标的名称"],
        "optional": ["采购品目名称", "数量", "标的金额", "所属行业"],
        "min_mandatory": 1,
        "min_optional": 2,
    },
    # 自定义产品/报价清单
    "PRODUCT_LIST": {
        "mandatory": [],
        "optional": ["产品名称", "品名", "规格型号", "规格", "单价", "数量", "单位"],
        "min_mandatory": 0,
        "min_optional": 3,
    },
    # 评分表
    "SCORING": {
        "mandatory": ["评分标准"],
        "optional": ["评分因素", "评审因素", "分值", "分数", "权重", "评分因素"],
        "min_mandatory": 1,
        "min_optional": 1,
    },
    # 响应应答表（招标要求 vs 投标应答）
    "RESPONSE_FORM": {
        "mandatory": ["招标要求", "投标应答"],
        "optional": [],
        "min_mandatory": 2,
        "min_optional": 0,
    },
    # 比选响应表
    "BID_RESPONSE_FORM": {
        "mandatory": ["比选要求", "响应内容"],
        "optional": [],
        "min_mandatory": 2,
        "min_optional": 0,
    },
}
```

### 输出结构

```python
{
    "preliminary": {"table_no": 1, "rows": [{"key": "...", "value": "..."}]},
    "product_lists": [
        {"table_no": 2, "headers": [...], "items": [{"品名": "...", "规格": "..."}]},
    ],
    "scoring": {"table_no": 11, "headers": [...], "dimensions": [...]},
    "response_forms": [...],
    "other_tables": [...],  # 合同模板等不解析的表格
}
```

### 集成方式

`table_classifier.py` 独立模块，被 `start_analyze_v3()` 调用。分类结果传给三个 Phase 使用：

```
table_results = classify_all_tables(doc.tables)
# Phase 1: 取 table_results["preliminary"] → 键值对 + 覆盖 metadata
# Phase 3: 取 table_results["product_lists"] → 写入 packages[].parameters
# Phase 3: 取 table_results["scoring"] → 补充/验证评分数据
```

---

## 2. 须知前附表 → metadata 融合

### 当前做法
前附表的键值对被 flatten 到纯文本，由 regex 逐个字段提取。

### 新做法
直接从前附表的键值对中提取：

```python
# 前附表行示例：
# | 10 | 评标办法 | 综合评分法 |
# | 11 | 联合体投标 | 不允许 |
# | 12 | 投标保证金 | 免收 |

# 规则映射：
PRELIMINARY_TO_METADATA = {
    "评标办法": "evaluation_method",
    "项目预算": "budget.total",
    "采购预算": "budget.total",
    "预算金额": "budget.total",
    "联合体投标": "allow_consortium",
    "是否允许联合体": "allow_consortium",
    "投标保证金": "bid_security_required",
    "投标有效期": "key_dates.bid_validity_days",
    "履约保证金": "performance_security_pct",
    "分包": "package_count",
    "是否允许进口产品": "allow_import",
    "采购代理服务费": "extra.agency_fee",
    "报价方式": "extra.pricing_rule",
    "现场踏勘": "extra.site_visit",
    "是否分包": "package_count",
}
```

表格提取优先级 > regex 提取（因为表格是比正文更可靠的信息源）。

---

## 3. 预设清单双层分类 + 章节模板

### 模板分层

基于文档类型分析（10 份标书验证）：

```python
# 章节结构模板（按 doc_type）
CHAPTER_TEMPLATES = {
    "TENDER": [
        "投标邀请", "投标人须知", "投标文件格式",
        "资格要求", "技术商务要求", "评标办法", "合同"
    ],
    "SELECTION": [
        "比选邀请", "比选须知", "供应商资格条件要求",
        "资格证明材料", "项目要求", "响应文件格式", "评审方法", "合同"
    ],
    "NEGOTIATION": [
        "谈判邀请", "供应商须知", "响应文件格式",
        "资格要求", "技术要求", "谈判程序", "合同"
    ],
    "INQUIRY": [
        "询价邀请", "供应商须知", "报价要求", "合同"
    ],
    # 竞争性磋商
    "CONSULTATION": [
        "磋商邀请", "磋商须知", "响应文件格式",
        "资格性审查", "技术要求", "磋商程序", "合同"
    ],
}
```

这些模板用于：
1. 章节定位的**初始锚点**（知道去哪个章节找什么）
2. 自动化验证（如果某章缺失，标记为"可能遗漏"）

### 当前 ELIGIBILITY_TEMPLATES 改造

已在前一版 spec (`layered-presets/spec.md`) 中设计。`_BASE` + `bid_type` + `doc_type` 三层合并。

---

## 4. to_dict v3 路径修复

同 `fix-to_dict-v3/spec.md`。

---

## 5. 章节定位增强 + 技术参数表增强

同 `enhance-section-anchoring/spec.md` 和 `enhance-tech-params/spec.md`。
