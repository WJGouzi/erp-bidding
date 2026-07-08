## Why

check-items 接口（`GET /bidding/tasks/:id/check-items`）的 `business` 和 `technical` 两个模块只从 `BiddingAnalysisResult` 的扁平 DB 列读取数据，忽略了 `analysis_data` JSON 中已有的丰富结构化数据（段级提取结果、表格分类结果、元数据扩展字段）。这导致大量商务要求和技术要求在核对面板中丢失或过于简略，用户在核对阶段看不到完整信息。

## What Changes

- **`assemble_business` 重写**：从 `analysis_data._comprehensive.business_requirements`、表格分类结果、`metadata.extra`、DB 列四源合并
- **`assemble_technical` 重写**：从 `analysis_data._comprehensive.technical_requirements`、表格分类技术要求表、产品清单表、DB 列四源合并
- **`EXTRA_LABELS` 键名修复**：修复 `analysis_schema.py` 中 3 个与实际 extraction 输出不匹配的键名（`submission_location`→`bid_submission_location` 等）
- 接口签名和输出 schema 不变，不影响前端和 `save_review`

## Capabilities

### New Capabilities
- `check-items-reconciliation`: 统一 check-items 各模块从 `analysis_data` 提取数据的策略，包括多源合并、去重、优先级回退

### Modified Capabilities
- 无（现有 spec 的行为不变，只是数据源更丰富）

## Impact

- `app/service_modules/task_pipeline/analysis_v3/check_items/business.py` — 重写
- `app/service_modules/task_pipeline/analysis_v3/check_items/technical.py` — 重写
- `app/domain/analysis_schema.py` — `EXTRA_LABELS` 键名修正
- 无 API 接口变化，无 schema 变化，无数据库迁移
