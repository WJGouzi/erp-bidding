# 混合解析管线 — 设计

## 架构总览

```
┌────────────────────────────────────────────────────────────┐
│                    原始 DOCX / PDF                          │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│  文档解析层 (规则)                                          │
│  ┌──────────────────┐  ┌────────────────────────────────┐  │
│  │ document_parser  │  │ table_parser + table_classifier│  │
│  │ → sections       │  │ → 表格分类+提取KV               │  │
│  │ → tables         │  │ → 前附表/评分表/产品清单        │  │
│  │ → raw text       │  │                                │  │
│  └──────────────────┘  └────────────────────────────────┘  │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│  Phase 1: 元数据提取                                         │
│                                                             │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │ 规则部分 (phase1)    │    │ LLM部分 (phase1_llm)       │  │
│  │                     │    │                             │  │
│  │ 项目编号/名称  ✅    │    │ 购买人/代理名称  ← 5种封面  │  │
│  │ 表格分类       ✅    │    │ 预算提取+分包分配 ← 千分位  │  │
│  │ 分包结构检测   ✅    │    │ 关键日期提取     ← 格式多变  │  │
│  │ 前附表KV融合   ✅    │    │                             │  │
│  └─────────────────────┘    └────────────────────────────┘  │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│  Phase 2: 生死线扫描 (eligibility)                          │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │ 规则部分             │    │ LLM部分                     │  │
│  │ 资格模板匹配    ✅   │    │ 非标资格条件提取 ← 自定义    │  │
│  │ 废标条件匹配    ✅   │    │                             │  │
│  │ ★条款提取       ✅   │    │                             │  │
│  └─────────────────────┘    └────────────────────────────┘  │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│  Phase 3: 得分点拆解 (scoring)                              │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │ 规则部分             │    │ LLM部分                     │  │
│  │ 技术参数统计    ✅   │    │ 评分表结构化    ← 表头多变   │  │
│  │ ★/▲参数统计    ✅   │    │ 商务要求抽取    ← 章节多变   │  │
│  │ 产品清单提取    ✅   │    │ 技术要求抽取    ← 章节多变   │  │
│  └─────────────────────┘    └────────────────────────────┘  │
└────────────────────────┬───────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│  合并输出 → analysis_data                                    │
│  规则结果 + LLM结果 → 规则结果优先覆盖                       │
└────────────────────────────────────────────────────────────┘
```

## 1. 规则修复（现有代码修补）

### 1.1 前附表分类（table_parser.py）

**问题**：`classify_table` 对 PRELIMINARY_TABLE 要求 `["内容", "说明与要求"]`，但多数文档用"说明和要求"

**修复**：放宽匹配
```python
# 修改前
"header_keywords": ["内容", "说明与要求"],

# 修改后
"header_keywords": ["说明"],  # 只要有"说明"就可能是前附表
"header_any": ["内容", "说明与要求", "说明和要求", 
                "应知事项", "条款名称", "须知事项"],
```

同时放宽列数限制：3列→2到4列（某些前附表有合并单元格）。

### 1.2 分包检测（__init__.py + phase1_metadata.py）

**问题**：只匹配 "共计X个包" / "第X包"，但最常见的是 "采购包X"

**修复**：
```python
# 新增 pattern
("package_count", r"采购包(\d+)"),  # 提取最大包号
```

检测逻辑改为：
1. 先查显式声明："本项目共X个包"
2. 没找到则扫描全文所有 "采购包X"，取最大编号
3. 如果所有采购包引用都来自同一个包号 → 可能只有一个包

### 1.3 KEY_MAP 扩展（phase1_metadata.py）

**问题**：前附表 key 变体没覆盖

**修复**：在现有子串匹配基础上，增加常见变体：
```python
KEY_MAP_FUZZY = {
    "采购预算": ("budget", "parse_money"),
    "预算金额": ("budget", "parse_money"),
    "评标办法": ("evaluation_method", "value"),
    "评审方法": ("evaluation_method", "value"),
    "比选方法": ("evaluation_method", "value"),
    "是否接受联合体": ("allow_consortium", "parse_bool_negated"),
    "是否允许联合体": ("allow_consortium", "parse_bool"),
    "投标保证金": ("bid_security_required", "parse_bool"),
    "履约保证金": ("performance_security_pct", "parse_pct"),
    "投标有效期": ("key_dates.bid_validity_days", "parse_int"),
    "代理服务费": ("extra.agency_fee", "parse_money"),
    "交货": ("extra.delivery", "value"),  # 匹配 "交货期" "交货时间" "交货地点"
    "服务期限": ("extra.service_period", "value"),
    "采购方式": ("bid_type", "value"),
    "是否分包": ("package_count", "parse_package"),
    "分包": ("package_count", "parse_package"),
    "报价方式": ("extra.pricing_rule", "value"),
    "现场踏勘": ("extra.site_visit", "parse_bool"),
    "是否允许进口": ("extra.allow_import", "parse_bool"),
    "质量要求": ("extra.quality_standard", "value"),
    "验收": ("acceptance_standard", "value"),
    "付款": ("payment_terms", "value"),     # 匹配 "付款方式" "付款进度"
    "支付方式": ("payment_terms", "value"),
    "结算方式": ("payment_terms", "value"),
}
```

### 1.4 parse_money 升级

**问题**：不支持千分位格式（1,033,302.36）

**修复**：
```python
def parse_money(text):
    # 先去掉千分位逗号
    cleaned = re.sub(r'(?<=\d),(?=\d)', '', text)
    # 再提取数字
    m = re.search(r'(\d+\.?\d*)', cleaned)
    if m:
        return float(m.group(1))
    return 0
```

## 2. LLM 增强（新增模块）

### 2.1 模块结构

```
app/service_modules/task_pipeline/analysis_v3/
├── llm_extractor.py          # LLM 提取统一入口
│   ├── extract_metadata()    # 购买人/代理/预算/日期
│   ├── extract_business()    # 商务要求
│   ├── extract_technical()   # 技术要求
│   └── extract_scoring()     # 评分表结构化
├── llm_prompts.py            # 所有 prompt 模板
└── llm_validator.py          # LLM 输出校验+兜底
```

### 2.2 LLM 调用设计

每个 LLM 调用使用独立的、高度结构化的 prompt：

```python
# llm_extractor.py

def extract_metadata(doc_text: str, tables_kv: dict) -> dict:
    """从文档前几页提取购买人/代理/预算/日期。
    
    输入: 文档前3000字符 + 前附表KV对
    输出: 结构化 JSON
    """
    prompt = f"""你是一个招标文件解析专家。从以下文档片段中提取信息。

文档片段：
{document_excerpt}

前附表关键信息：
{table_summary}

请提取以下字段，以 JSON 格式返回：
{{
  "purchaser_name": "采购人名称（全称）",
  "purchaser_contact": "联系人",
  "agent_name": "代理机构名称（全称）",
  "agent_contact": "联系人",
  "budget_total": 总预算金额（数字，无千分位逗号）,
  "budget_note": "预算说明（含单位万元/元等）",
  "budget_packages": [
    {{"package_no": 1, "amount": 数字, "note": "说明"}}
  ],
  "bid_deadline": "投标截止时间",
  "bid_opening": "开标时间",
  "bid_validity_days": 投标有效期天数
}}

注意：
- 预算金额可能带千分位逗号（1,033,302.36），去掉逗号转数字
- 预算可能分布在各个包中，请分别提取
- 如果文档中没有对应字段，返回 null 或空数组
"""
    # 调用 LLM，限制 max_tokens=500
    return call_llm_json(prompt, max_tokens=500)
```

### 2.3 各 LLM 调用的输入和约束

| 调用点 | 输入 | 输出 | max_tokens | 兜底策略 |
|--------|------|------|-----------|----------|
| `extract_metadata` | 文档前3000字 + 前附表KV | 购买人/代理/预算/日期 | 500 | 规则正则兜底 |
| `extract_business` | "商务要求"章节全文 | 结构化商务要求列表 | 800 | 返回空列表 |
| `extract_technical` | "技术要求"章节全文 | 结构化技术参数列表 | 800 | 返回空列表 |
| `extract_scoring` | 评分表原始文本 | 评分维度结构化 | 1000 | 返回空列表 |

## 3. 集成方式（在现有管线中插入）

```python
# __init__.py 中修改 start_analyze_v3 的逻辑

def start_analyze_v3(task_id, ...):
    # 1. 规则 Phase 1（保持不变）
    metadata = extract_metadata(meta_text, file_name=file_name, table_results=table_results)
    
    # 2. LLM 增强（新增）
    if _should_use_llm(doc):
        llm_meta = llm_extractor.extract_metadata(doc_text, table_kv)
        metadata = _merge_llm_into_metadata(metadata, llm_meta)
        # LLM 结果覆盖规则结果（LLM 更灵活）
        # 但规则有值的字段保留规则值（规则更精确）
    
    # 3. 后续 Phase 2/3 保持不变
    ...
```

合并逻辑：
```python
def _merge_llm_into_metadata(rule_meta, llm_meta):
    """LLM 结果合并到规则结果。规则有值则保留规则值。"""
    for key in ['purchaser_name', 'agent_name', 'budget_total', 
                'bid_deadline', 'bid_opening', 'bid_validity_days']:
        if not rule_meta.get(key) or rule_meta.get(key) in (0, '', 0.0):
            if llm_meta.get(key):
                rule_meta[key] = llm_meta[key]
    return rule_meta
```

## 4. 错误处理

| 场景 | 处理方式 |
|------|----------|
| LLM 调用超时 | 降级到宽松规则兜底 |
| LLM 返回非 JSON | 重试1次，失败后降级 |
| LLM 返回空值 | 保留规则值（不覆盖） |
| LLM 明显异常 | 规则校验层拦截 |
