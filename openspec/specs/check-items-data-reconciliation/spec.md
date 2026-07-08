# check-items 数据源重构 — 设计方案

## 1. 问题

check-items 接口（`GET /bidding/tasks/:id/check-items`）的各模块对 `analysis_data` 的使用不一致：

- `bidding_info` ✅ 正确使用 `analysis["metadata"]`
- `qualification` ✅ 正确使用 `analysis["eligibility"]`
- `scoring` ✅ 正确使用 `analysis["scoring"]`
- `packages` ✅ 混合使用 `result.packages_json` + `analysis["metadata"]`
- **`business`** 🔴 只读 `result.business_requirements` 扁平 DB 柱，忽略 `analysis_data` 中已有的结构化数据
- **`technical`** 🔴 只读 `result.technical_requirements` 扁平 DB 柱，忽略 `analysis_data` 中已有的结构化数据

同时 `analysis_schema.py` 中的 `EXTRA_LABELS` 有 3 个键名与实际 extraction 输出不匹配，导致 `computed_business_requirements` 拿不到对应数据。

---

## 2. 数据源全景

### 2.1 `analysis_data`（v3 结构化输出）

```
analysis_data (dict)
├── metadata
│   ├── project_name, project_code, purchaser, agent, budget, key_dates
│   ├── extra              ← 16+ 商务相关字段
│   └── evaluation_method, document_type, ...
├── eligibility
│   ├── qualifications[]   ← 24 项资格要求
│   ├── disqualifications[]
│   └── starred_requirements[]
├── scoring
│   ├── method, total_score
│   └── dimensions[]       ← 5 个评分维度
├── packages[]             ← 分包信息
├── table_classification
│   ├── product_lists[]    ← 产品清单表（含规格/单价/交货期）
│   ├── tech_requirements[]
│   ├── business_requirements[]
│   ├── service_requirements[]
│   ├── scoring
│   ├── qualification_checks[]
│   ├── response_forms[]
│   └── other_tables[]
├── _comprehensive         ← v3 组装器输出
│   ├── business_requirements[]  ← 结构化商务要求
│   ├── technical_requirements[] ← 结构化技术要求
│   ├── products[]
│   ├── eligibility, scoring
│   └── ...
├── format_requirements
├── bidder_notice
└── _segments[]            ← 原始段级结果
```

### 2.2 `BiddingAnalysisResult` DB 列（扁平文本）

| 列 | 说明 |
|---|---|
| `business_requirements` | 文本串，由 `analysis.py` 从 extra + 表格分类拼接 |
| `technical_requirements` | 文本串，同上 |
| `qualification_requirements` | JSON 数组 |
| `scoring_items` | JSON 数组 |
| `disqualification_items` | JSON 数组 |
| `packages_json` | JSON 数组 |

---

## 3. 当前数据流（问题态）

```
                 analysis_data (v3)
                 ┌─────────────────┐
                 │ _comprehensive   │── business_requirements[] ──╌╌╌╌→ ❌ 被忽略
                 │  .business_reqs  │── technical_requirements[]──╌╌╌╌→ ❌ 被忽略
                 │ table_           │── product_lists[] ──────────╌╌╌╌→ ❌ 被忽略
                 │  classification  │── biz_req_tables[] ─────────╌╌╌╌→ ❌ 被忽略
                 │ metadata.extra   │── payment_terms, delivery…──╌╌╌╌→ ❌ 被忽略
                 └─────────────────┘
                          │
                 DB 扁平列 (仅这些进入 check-items)
                 ┌─────────────────┐
                 │ business_       │──→ assemble_business()
                 │ requirements    │       ↓
                 │ technical_      │──→ assemble_technical()
                 │ requirements    │       ↓
                 └─────────────────┘      items: 1项 (丢失)
```

---

## 4. 目标数据流

```
                 analysis_data (v3)
                 ┌──────────────────────────┐
                 │ _comprehensive            │
                 │  .business_requirements[]─╌╌╌┐
                 │  .technical_req[]─────────╌╌╌├──→ 结构化 / 表格提取优先
                 │ table_classification      │  │
                 │  .product_lists[]─────────╌╌╌┤
                 │  .biz_requirements[]──────╌╌╌┤
                 │  .tech_requirements[]─────╌╌╌┤
                 │  .service_requirements[]──╌╌╌┤
                 │ metadata.extra            │  │
                 │  .delivery_location───────╌╌╌┤
                 │  .payment_terms───────────╌╌╌┤
                 │  .bid_submission_location─╌╌╌┤
                 │  .special_declaration─────╌╌╌┘
                 └──────────────────────────┘  │
                          ↓                    ↓
                 ┌────────────────┐   ┌────────────────┐
                 │ assemble_      │   │ assemble_      │
                 │ business()     │   │ technical()    │
                 │  3 源合并      │   │  3 源合并      │
                 └────────────────┘   └────────────────┘
                          │                    │
                 DB 扁平列  ↓ 兜底              ↓ 兜底
                 ┌──────────────┐   ┌──────────────┐
                 │ business_    │   │ technical_   │
                 │ requirements │   │ requirements │
                 └──────────────┘   └──────────────┘
```

---

## 5. 模块修改方案

### 5.1 `assemble_business` — 三源合并

```python
def assemble_business(result, analysis: dict) -> dict:
    """组装商务要求：优先 analysis_data 结构化源，DB 列兜底。"""
    items = []
    seen = set()  # 去重

    # 源 A: _comprehensive 结构化商务要求
    for br in analysis.get("_comprehensive", {}).get("business_requirements", []):
        text = br.get("requirement", "")
        if text and text not in seen:
            seen.add(text)
            items.append({"content": text, "source_section": "comprehensive"})

    # 源 B: 表格分类中的商务要求表
    for table in analysis.get("table_classification", {}).get("business_requirements", []):
        for item in table.get("items", []):
            text = item.get("商务要求内容", "") or item.get("商务要求名称", "")
            if text and text not in seen:
                seen.add(text)
                items.append({"content": text, "source_section": "table_business"})

    # 源 C: 表格分类中的服务要求表（原文归入商务范畴）
    for table in analysis.get("table_classification", {}).get("service_requirements", []):
        for item in table.get("items", []):
            text = item.get("服务要求内容", "") or item.get("服务要求名称", "")
            if text and text not in seen:
                seen.add(text)
                items.append({"content": text, "source_section": "table_service"})

    # 源 D: metadata.extra 中的商务字段
    extra = analysis.get("metadata", {}).get("extra", {})
    if isinstance(extra, dict):
        # 按 EXTRA_LABELS 顺序输出，保证可预测性
        for field_key, field_label in EXTRA_LABELS:
            val = extra.get(field_key, "")
            if val and str(val).strip():
                text = f"{field_label}：{val}"
                if text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": f"extra.{field_key}"})
        # 补充 extra 中有但 EXTRA_LABELS 未覆盖的字段（如 bid_submission_location）
        extra_only_keys = {
            "bid_submission_location": "递交地点",
            "file_purchase_price": "文件售价",
            "winner_count_text": "成交数量",
        }
        for field_key, field_label in extra_only_keys.items():
            val = extra.get(field_key, "")
            if val and str(val).strip():
                text = f"{field_label}：{val}"
                if text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": f"extra.{field_key}"})

    # 源 E: DB 扁平列兜底（仅在结构化源都为空时使用）
    if not items:
        biz_text = result.business_requirements or ""
        if biz_text.strip():
            for line in biz_text.split("\n"):
                line = line.strip()
                if line and line not in seen:
                    seen.add(line)
                    items.append({"content": line, "source_section": "db_fallback"})

    return {"items": items, "raw": ""}
```

### 5.2 `assemble_technical` — 三源合并

```python
def assemble_technical(result, analysis: dict) -> dict:
    """组装技术要求：优先 analysis_data 结构化源，DB 列兜底。"""
    items = []
    seen = set()

    # 源 A: _comprehensive 结构化技术要求
    for tr in analysis.get("_comprehensive", {}).get("technical_requirements", []):
        text = tr.get("requirement", "")
        if text and text not in seen:
            seen.add(text)
            items.append({"content": text, "source_section": "comprehensive"})

    # 源 B: 表格分类中的技术要求表
    for table in analysis.get("table_classification", {}).get("tech_requirements", []):
        for item in table.get("items", []):
            name = item.get("技术要求名称", "")
            params = item.get("技术参数与性能指标", "")
            if name and params:
                text = f"{name}: {params}"
                if text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": "table_tech"})

    # 源 C: 产品清单表中的规格参数
    for table in analysis.get("table_classification", {}).get("product_lists", []):
        for item in table.get("items", []):
            name = item.get("采购产品名称", "") or item.get("产品名称", "")
            spec = (item.get("★规格参数", "") or item.get("技术参数与性能指标", "")
                    or item.get("规格参数", "") or item.get("规格", ""))
            if name:
                if spec:
                    text = f"{name}: {spec}"
                else:
                    text = name
                if text not in seen:
                    seen.add(text)
                    items.append({"content": text, "source_section": "product_list"})

    # 源 D: DB 扁平列兜底
    if not items:
        tech_text = result.technical_requirements or ""
        if tech_text.strip() and not _is_placeholder(tech_text):
            for line in tech_text.split("\n"):
                line = line.strip()
                if line and line not in seen:
                    seen.add(line)
                    items.append({"content": line, "source_section": "db_fallback"})

    return {"items": items, "raw": ""}
```

### 5.3 `EXTRA_LABELS` 键名修复

修改 `analysis_schema.py` 中的 `EXTRA_LABELS`：

| 当前键名 | 实际 extra 键名 | 修复方案 |
|---|---|---|
| `submission_location` | `bid_submission_location` | 改为 `bid_submission_location` |
| `winner_count` | `winner_count_text` | 改为 `winner_count_text` |
| `submission_docs` | `submission_docs_summary` | 改为 `submission_docs_summary` |
| `after_sale_service` | 不存在 | 保留，仅在 extra 中有值时输出 |
| `packaging_transport` | 不存在 | 保留 |
| `insurance` | 不存在 | 保留 |
| `delivery_terms` | 不存在 | 保留 |

同时补充缺失的常用字段：

| 新增键名 | 标签 |
|---|---|
| `file_purchase_price` | 文件售价 |
| `bid_submission_location` | 递交地点（覆盖旧键） |

> 注意：`build_business_requirements` 和 `computed_business_requirements` 共享 `EXTRA_LABELS`，改动影响两个路径。旧字段名可以保留别名兼容。

---

## 6. 影响范围

### 6.1 改动的文件

| 文件 | 改动 |
|---|---|
| `app/service_modules/.../check_items/business.py` | 重写 `assemble_business`：三源合并 |
| `app/service_modules/.../check_items/technical.py` | 重写 `assemble_technical`：三源合并 |
| `app/domain/analysis_schema.py` | 修复 `EXTRA_LABELS` 键名匹配 |

### 6.2 不改动的文件

- `check_items/__init__.py` — Facade 入口不动，`assemble_business(result, analysis)` 签名不变
- `check_items/bidding_info.py` — 已正确
- `check_items/qualification.py` — 已正确
- `check_items/scoring.py` — 已正确
- `check_items/packages.py` — 已正确
- `check_items/checklist.py` — 已正确
- `api/tasks.py` — 不改接口签名
- `pipeline/analysis.py` `get_check_items()` — 不改调用方

### 6.3 输出 schema 向后兼容

```json
{
  "items": [
    {"content": "...", "source_section": "..."}
  ],
  "raw": ""
}
```

前端 `save_review` 存储的字段结构不变，不影响已存档数据。

---

## 7. 风险与回退

| 风险 | 概率 | 缓解 |
|---|---|---|
| 结构化源数据为空，回退到 DB 列 | 高 | 回退是显式的兜底逻辑，输出和现在一样 |
| 结构化源与 DB 列内容重复 | 中 | `seen` set 去重，按优先级去重 |
| extra 字段名在新文档中再次变化 | 低 | 先修已知 3 个不匹配，后续发现再补 |
| `_comprehensive` 的数据质量低于预期 | 中 | 排在表格分类之后，DB 列之前，数据源优先级可调 |

---

## 8. 验证要点

1. 同一份招标文件，对比修改前后 `GET /check-items` 的 `business.items` 和 `technical.items` 数量和质量
2. 确认 `save_review` 不再丢失结构化数据
3. 确认 `EXTRA_LABELS` 修复后 `computed_business_requirements` 能正确输出 `bid_submission_location`
4. 确认三段数据源齐全的文档，输出不重复、不丢失
