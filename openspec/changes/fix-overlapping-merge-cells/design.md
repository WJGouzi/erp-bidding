## Context

当前标书生成流程中，表格合并单元格的渲染存在两个缺陷：

**缺陷 1 — content_blocks 路径重叠合并崩溃** (`helpers.py:~4196`)：当 parser 从原始 Word XML 中提取的 `merge_cells` 列表包含同一 `(row, col)` 的 horizontal 和 vertical 条目时（即原单元格同时有 `gridSpan` 和 `vMerge` 属性），渲染器顺序执行合并操作，第二次操作因引用已消耗的单元格而抛出 `requested span not rectangular` 异常。

**缺陷 2 — template_content 路径缺少垂直合并** (`helpers.py:~4479`)：该路径的 merge 循环只处理了 `type == "horizontal"` 的情况，所有垂直合并条目被静默忽略。

## Goals / Non-Goals

**Goals:**
- 消除合并单元格渲染时的 `requested span not rectangular` 异常
- 使 `template_content` 路径正确处理垂直合并
- 保持所有现有功能不变（非模板章节、纯文本等）

**Non-Goals:**
- 不修改解析器（`document_parser.py`）的 merge_cells 提取逻辑
- 不修改数据传输层（JSON 序列化/反序列化）
- 不修改数据模型

## Decisions

### 决策 1：矩形合并法

**方案选择**：对同一 `(row, col)` 上重叠的 horizontal + vertical 合并，不再逐条执行，而是整合为单个矩形合并。

**具体做法**：
1. 遍历 `merge_cells` 列表，按 `(row, col)` 分组
2. 对每个分组，取最大的 `v_span` 和 `h_span`
3. 执行 `cells[row][col].merge(cells[row + v_span - 1][col + h_span - 1])`

**替代方案拒绝**：
- ❌ 跳过冲突合并：会丢失信息，被跳过单元格的内容无法合并
- ❌ 变换合并顺序（先大后小）：不保证所有情况都能覆盖
- ❌ 在 parser 侧去重：parser 应忠实反映原始 XML，渲染层负责适配

### 决策 2：两处渲染路径统一处理

`content_blocks` 路径和 `template_content` 路径的表格合并逻辑提取为同一策略，避免重复修复。

**具体做法**：在两处代码中都实现相同的矩形合并逻辑（函数级别的代码重复可接受，提取公共函数的成本高于收益）。

## Risks / Trade-offs

- [低风险] 无重叠的简单合并（仅有 horizontal 或仅有 vertical）不受影响
- [低风险] 矩形合并后所有单元格均在同一个合并区域内，内容顺序由 python-docx 决定（concatenates），与原期望一致
- [中风险] 极少数极端复杂的表格可能存在多层级嵌套合并（3+ 个合并重叠），目前未测试到——可视情况在后续迭代中增强
