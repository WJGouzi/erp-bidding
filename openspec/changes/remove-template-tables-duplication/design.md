## Context

当前 `phase1_5_format.py` 在提取格式要求章节时，会为每个 required_section 生成两套表格数据：

- `template_content`: 按文档原始顺序保存的 ContentBlock 数组（text / table 穿插）
- `template_tables`: 从同一 `section.content` 中独立提取的纯表格数组

两份数据来自同一数据源，但格式不同（`template_content` 使用 `per_cell_data`，`template_tables` 使用扁平的 `headers/rows/merge_cells`），且 `template_tables` 丢失了表格在文字流中的顺序信息。渲染时必须做额外的对齐才能正确还原文字和表格的混合排版。

## Goals / Non-Goals

**Goals:**
- 消除 `template_tables` 冗余，所有消费方统一从 `template_content` 获取表格数据
- 确保前后输出完全一致（内容不丢失、顺序不紊乱）
- 清理不再需要的辅助函数
- DOCX 表格边框渲染为黑色实线

**Non-Goals:**
- 不改变分析阶段的数据契约（`analysis_data` 的 JSON 结构不变）
- 不涉及 `ContentBlock` 或 `Section` 的底层数据模型修改
- 不调整格式提取的置信度逻辑

## Decisions

### 决定 1：删除 `template_tables`，不保留兼容字段

**方案**：直接从 `_extract_required_sections()` 中移除 `template_tables` 的构建和写入。

**理由**：
- 目前消费方（`template_binder.py`）已通过 `template_content` 消费表格数据，`template_tables` 主要用于 `catalog.py`/`helpers.py` 中的独立表格查询（如通过 `n_tables` 获取产品信息表）
- 这些查询可以改为从 `template_content` 中过滤 `type == "table"` 的块实现，改动量小

### 决定 2：`template_content` 中的 table 块同时携带 `per_cell_data` 和扁平格式

**方案**：在 `template_content` 的 table 块中，除了保留现有的 `per_cell_data`，额外嵌入 `headers`、`rows`、`merge_cells` 等扁平字段，让消费方无需解码 `per_cell_data` 即可直接使用。

**理由**：
- 降低消费方的迁移成本
- `template_content` 与 `template_tables` 的数据在同一个块内，不存在不一致问题

### 决定 3：DOCX 表格边框单独设置

**方案**：在构建 DOCX 的渲染函数中，对每个表格应用黑色实线边框样式。

**理由**：纯渲染层改动，不影响数据层。

## Risks / Trade-offs

- **[兼容性] 存量已存储的 `format_requirements` 数据仍包含 `template_tables`** → 生成标书时从数据库读取旧数据仍可正常工作（下游消费方优先读 `template_content`，`template_tables` 作为冗余字段被忽略）
- **[遗漏消费方] 可能有未发现的 `template_tables` 引用** → `rg -rn "template_tables\|n_tables" --type py` 已确定所有引用点

## Data Flow 变更

```
Before:
  section.content → template_content [{type:text}, {type:table, per_cell_data}, {type:text}]
                  → template_tables [{headers, rows}]  ← 冗余
        
  渲染时: template_content 渲染段落 + template_tables 渲染表格（需对齐）

After:
  section.content → template_content [{type:text}, {type:table, per_cell_data, headers, rows}, {type:text}]
                   ~~template_tables~~ (已删除)
        
  渲染时: template_content 按顺序渲染（段落跳过，表格渲染），无需对齐
```
