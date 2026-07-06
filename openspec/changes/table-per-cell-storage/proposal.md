## Why

标书生成过程中，表格的「识别 → 存储 → 组装」三阶段存在严重的信息流失。当前系统有 3 条并行的表格存储路径（ContentBlock、raw_tables、纯文本），各路径保留的信息不一致。最终渲染时合并信息丢失、列宽退化、格式硬编码，导致生成的标书表格与原始招标文件表格不一致（表格结构错误、合并单元格丢失、文字与表格顺序错乱）。

## What Changes

1. **统一的 Per-Cell 自描述存储格式** — 每个单元格携带 colSpan、rowSpan、字体、对齐等属性，替代当前的 3 条碎片化路径
2. **识别阶段补充** — `_extract_raw_table()` 增加列宽和单元格格式提取
3. **存储阶段统一** — 所有表格数据统一走同一个存储格式，消除多路径不一致
4. **组装阶段重构** — `_write_table_from_lines()` 改为从 per-cell 格式直接生成 XML，还原原始列宽和格式
5. **保留当前正确的合并检测逻辑**（水平合并 gridSpan、垂直合并 vMerge 的去重和跨度计算不动）

## Capabilities

### New Capabilities

- `table-per-cell-model`: Per-Cell 自描述表格数据模型定义（存储格式 schema、序列化/反序列化）
- `table-extract-enhance`: 识别阶段增强 — 在现有 `_extract_raw_table()` 基础上增加列宽和单元格格式提取
- `table-assembly`: 组装阶段改造 — 从 per-cell 格式直接还原 docx 表格 XML

### Modified Capabilities

- (无现有 spec 需要修改，此为全新的表格管道重塑)

## Impact

- `app/infrastructure/table_classifier.py` — `_extract_raw_table()` 增加列宽/格式提取，数据结构变更
- `app/infrastructure/table_parser.py` — 可能调整分类策略（不再依赖章节类型决定走哪条路）
- `app/infrastructure/document_parser.py` — `ContentBlock` 表格字段增强
- `app/service_modules/task_pipeline/helpers.py` — `_write_table_from_lines()` 重写，生成环节重构
- `app/service_modules/task_pipeline/template_binder.py` — `ContentBlock.table()` 适配新格式
- `tests/` — 新增测试覆盖 per-cell 格式的 round-trip 验证
