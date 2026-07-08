## Context

check-items 接口的 6 个模块中，4 个（`bidding_info`、`qualification`、`scoring`、`packages`）已经正确使用 `analysis_data` 的结构化数据。但 `business` 和 `technical` 两个模块只读取 `BiddingAnalysisResult` 的扁平 DB 列（`result.business_requirements` / `result.technical_requirements`），忽略了 `analysis_data` JSON 中已有的：

- `_comprehensive.business_requirements[]` / `_comprehensive.technical_requirements[]` — v3 段级提取的结构化列表
- `table_classification.business_requirements[]` / `table_classification.service_requirements[]` — 表格分类结果
- `metadata.extra.*` — 16+ 商务相关的元数据扩展字段

同时 `analysis_schema.py` 的 `EXTRA_LABELS` 中有 3 个键名与实际 extraction 输出不匹配，导致 `computed_business_requirements` 拿不到对应数据。

## Goals / Non-Goals

**Goals:**
- `assemble_business` 从多源（_comprehensive + 表格分类 + metadata.extra + DB 列）合并输出
- `assemble_technical` 从多源（_comprehensive + 表格分类技术表 + 产品清单 + DB 列）合并输出
- `EXTRA_LABELS` 键名修复，确保 `bid_submission_location` 等字段能被正确读取
- 去重保障：同一内容出现在多个数据源时只输出一次
- 兜底保障：所有结构化源均空时回退到现有 DB 列行为

**Non-Goals:**
- 不改动已正确的 4 个模块（bidding_info / qualification / scoring / packages）
- 不改动 API 接口签名和输出 schema
- 不改动 `save_review` 存储逻辑
- 不做数据库迁移
- 不引入新依赖

## Decisions

### 决策 1：多源合并 + 优先级回退，而非替换

- **方案**：`_comprehensive[]` > `table_classification[]` > `metadata.extra` > `DB 列兜底`
- **理由**：结构化源的质量和颗粒度逐级降低，按优先级取确保最佳数据；兜底保证零数据时行为不变
- **替代方案**：只从 `_comprehensive` 取（丢弃了一些表格数据）；只从 DB 列取（现状，信息丢失）

### 决策 2：`seen set` 文本去重

- **方案**：用 set 记录已加入的 content 文本，避免跨源重复
- **理由**：同一商务要求可能同时出现在 `_comprehensive` 和 `table_classification` 中，去重后前端不冗余

### 决策 3：EXTRA_LABELS 直接修正键名

- **方案**：将 `submission_location` → `bid_submission_location`，`winner_count` → `winner_count_text`，`submission_docs` → `submission_docs_summary`
- **理由**：实际 extraction 输出的键名带 `bid_` 或 `_text` 后缀，期望键名必须对齐
- **替代方案**：加别名兼容（过度设计，不如直接修正）

## Risks / Trade-offs

| 风险 | 概率 | 缓解 |
|---|---|---|
| 结构化源为空时回退到 DB 列，行为不变 | 高 | 兜底逻辑显式处理，零风险 |
| 多源间内容重复 | 中 | `seen` set 去重 |
| extra 字段名在不同文档中再次变化 | 低 | 先修已知 3 个，后续发现再补 |
| `_comprehensive` 数据质量低于预期（LLM 提取偏差） | 中 | 排在表格分类之后、DB 列之前，用户可在 check-items 面板修正 |
