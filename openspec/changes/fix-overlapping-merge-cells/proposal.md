## Why

当前标书生成过程中，包含复杂合并单元格（交织的水平+垂直合并）的表格渲染会失败。解析器正确提取了原始 Word 表格中的所有合并信息，但渲染器在逐条应用合并操作时，后一条合并引用的单元格已被前一条合并消耗，导致 `requested span not rectangular` 异常。具体表现为：
- "供应商基本情况表"（43 个 merge_cells）渲染时崩溃
- `template_content` 渲染路径完全忽略垂直合并

## What Changes

1. **content_blocks 表格渲染修复** (`helpers.py:~4196`): 将同一 `(row, col)` 上重叠的 horizontal + vertical 合并操作整合为单个矩形合并操作再执行
2. **template_content 表格渲染修复** (`helpers.py:~4479`): 补全垂直合并的处理逻辑，同样使用矩形合并法
3. **消耗单元格追踪**: 合并过程中追踪已消耗的单元格范围，避免冲突

## Capabilities

### New Capabilities
- `merge-cell-rectangular-render`: 将同一单元格上的水平和垂直合并整合为矩形合并操作。当 parser 提取的 merge_cells 中同一 (row,col) 同时存在 horizontal 和 vertical 条目时，渲染器将其合并为 `cells[row][col].merge(cells[row+v_span-1][col+h_span-1])` 的矩形操作，而非逐条执行。

### Modified Capabilities

- `table-merge-preservation` (from `fix-bidding-table-generation`): 在现有 merge_cells 提取能力的下游新增渲染修复要求——不仅提取和传输 merge_cells，还需正确处理重叠合并的渲染。

## Impact

- **Affected file**: `app/service_modules/task_pipeline/helpers.py` — 两处表格 merge_cells 渲染逻辑
- **不涉及**: parser、数据模型、API、数据库
- **风险**: 低——改动集中在渲染层的合并策略，不影响解析和数据传输
- **测试**: 需要验证含交织合并的复杂表格（如供应商基本情况表）输出正确
