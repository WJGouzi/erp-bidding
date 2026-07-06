## Context

`table-per-cell-storage` change 完成了 per-cell 数据模型和编解码器的实现：
- `TableCell` / `TableRow` / `TableData` 数据类
- `to_per_cell()`：旧三角格式 → TableData
- `write_table_from_data()`：TableData → docx XML（带 gridSpan/vMerge）
- `to_dict()` / `from_dict()`：JSON 序列化

但这些能力**从未接入生成管线**。组装阶段仍用旧方式写表格。

## 三个断裂点

### 断裂点 A：`template_binder.ContentBlock` 不携带 per_cell

`ContentBlock.table()` 工厂接受的参数为 `(headers, rows, merge_cells, column_widths)`，
`to_dict()` 序列化这些字段但不含 `per_cell`。

**修复**：ContentBlock 增加 `per_cell: Optional[dict]` 字段（存 `to_dict(TableData)` 的 JSON dict），
贯穿 `to_dict()` / 反序列化 / `_fill_table_block()`。

### 断裂点 B：`phase1_5_format.py` 子章节循环遗漏 per_cell

主循环（`for block in getattr(child, "content", [])`）生成 `per_cell`，
子章节循环（`for sub in getattr(child, "children", [])`）直接跳过。

**修复**：在子章节的 `template_content.append({"type":"table", ...})` 中也调用 `_build_per_cell()`。

### 断裂点 C：`helpers.py` 消费端不用 per_cell

两个路径都不使用 per_cell 数据：
- ContentBlock 路径用 `.merge()` API
- 旧文本路径用 `_write_table_from_lines()` 传空 merges

**修复**：两处均改为优先检测 `per_cell` → `from_dict()` → `write_table_from_data()`。
旧路径作为降级保留。

## 数据流（修复后）

```
解析/分析
  └─ phase1_5_format.py
       ├─ template_content[].per_cell ← to_dict(TableData)
       │
存储（MySQL JSON）
  └─ analysis_data.format_requirements.required_sections[].template_content[].per_cell
       │
绑定
  └─ template_binder.py
       ├─ bind_template(): 读取 per_cell → ContentBlock.per_cell
       ├─ _fill_table_block(): 保留 per_cell
       └─ to_dict(): 序列化 per_cell
            │
组装
  └─ helpers.py _write_outline_item()
       ├─ ContentBlock 路径: from_dict() → write_table_from_data()  ← 新路
       └─ 旧文本路径: from_dict() → write_table_from_data()         ← 新路
            │
渲染
  └─ docx XML: gridCol + gridSpan + vMerge  ✓
```

## 向后兼容

- `per_cell` 为可选字段，不存在时走旧路径
- `_write_table_from_lines()` 保留不动
- 现有测试无需修改

## 测试策略

1. 单元测试：`ContentBlock` 携带 per_cell 的序列化 round-trip
2. 集成测试：从 `_build_per_cell()` → `write_table_from_data()` → 验证生成的 XML 含 gridSpan/vMerge
3. 回归：现有 74 个表格测试不变
