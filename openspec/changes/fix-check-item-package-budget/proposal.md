## Why

检查 `check-items` 接口数据时发现多个数据缺失和错误：

1. **预算错误**：选包后 `bidding_info.budget` 显示项目总预算（1015万元），而非对应包的预算（第1包应显示274万元）。`metadata.budget.packages` 已有分包预算数据，但 `_extract_budget()` 只读了 `budget.total`。
2. **技术规格缺失**：`technical.items` 返回空数组。经溯源分析，docx 第六章包含 9 个包共 649 行的"规格型号及技术要求"数据表（Tables [19]-[27]），但 `table_classification` 被整体从管线中移除，未被分类提取。
3. **采购清单缺失**：docx 包含 9 个包共 654 行的采购清单表（Tables [10]-[18]），同样未被提取。
4. **评分项遗漏**：评分表 Table [30] 有 4 个维度（报价30分、规格型号及技术要求30分、业绩8分、配送方案32分），但只提取到 3 个，漏了"业绩8分"。

根因：`table_classifier.py` 被标记为 DEPRECATED 并从管线中移除，`analysis_data` 中显式 `pop("table_classification", None)`。分段分析（segmented）也未提取技术/商务要求，导致 `_comprehensive.technical_requirements` 始终为空。`format_requirements` 只负责投标文件格式模板，不负责数据表提取，两者是独立路径。

## What Changes

- **恢复数据表分类**：复活 `table_classifier.py`，按表头特征识别三类数据表（技术规格表、采购清单表、评分表），输出到 `analysis_data.table_classification`。**不碰** `format_requirements` 路径（资格性文件格式要求不受影响）
- **打通 `_comprehensive`**：在组装器中从 `table_classification` 读取数据注入到 `_comprehensive.technical_requirements`、`products`、`scoring`
- **分包预算修复**：`_extract_budget()` 增加分级降级：包预算 → 总预算 → 空
- **check-items 模块验证**：确认现有多源合并代码在数据源恢复后自动生效；`scoring` 补齐遗漏的评分维度

## Capabilities

### New Capabilities

- `data-table-classification`: 数据表分类引擎——按表头特征识别技术规格表、采购清单表、评分表，提取结构化数据，不干扰资格性模板提取路径

### Modified Capabilities

- `package-budget-resolution`: 分包预算解析映射——从 `metadata.budget.packages` 或 `analysis_data.packages[].budget` 提取包预算

## Impact

- `app/infrastructure/table_classifier.py` — 复活，限于三类数据表（tech_requirements, product_lists, scoring），不碰资格性模板
- `app/service_modules/task_pipeline/analysis_v3/__init__.py` — 移除 `analysis_data.pop("table_classification")`，加入数据表分类调用
- `app/service_modules/task_pipeline/analysis_v3/assembler.py` — 从 `table_classification` 注入技术规格、产品、评分数据到 `_comprehensive`
- `app/service_modules/.../check_items/bidding_info.py` — 分包预算降级修复
- `app/service_modules/.../check_items/scoring.py` — 补齐遗漏评分维度
- `app/service_modules/.../check_items/technical.py` — 加诊断日志
- 无 API schema break，无数据库迁移
