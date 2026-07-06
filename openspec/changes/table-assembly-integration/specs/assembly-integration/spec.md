# 表格组装集成改造

## ADDED Requirements

### Requirement: ContentBlock 携带 per_cell 数据

ContentBlock 必须新增 per_cell 字段 SHALL be added to ContentBlock as `per_cell: Optional[dict]`.

- SHALL add `per_cell: Optional[dict]` 字段，默认 None
- `ContentBlock.table()` 工厂 SHALL 接收 `per_cell=None` 参数
- `ContentBlock.to_dict()` SHALL 在 per_cell 存在时序列化
- `bind_template()` SHALL 读取 `block_data.get("per_cell")` 传给工厂
- `_fill_table_block()` SHALL 保留 per_cell 不做更改

#### Scenario: ContentBlock 携带 per_cell 序列化和反序列化

```python
block = ContentBlock.table(["h1"], [["a"]], per_cell={"gridCols": [1000], "rows": [...]})
d = block.to_dict()
assert d["per_cell"]["gridCols"] == [1000]
```

### Requirement: phase1_5_format 子章节补全 per_cell

`phase1_5_format.py` 子章节循环的表格条目必须补全 per_cell。子章节表格 SHALL include per_cell in template_content.

- SHALL add `"per_cell": _build_per_cell(headers, rows, merge_cells, column_widths)` 到子章节的表格条目

#### Scenario: 子章节表格包含 per_cell

- 子章节有 table block 时，template_content 条目包含 `per_cell` 字段
- per_cell 结构正确（含 gridCols、rows、cells 等）

### Requirement: helpers.py 消费端优先使用 per_cell

组装阶段表格写入必须优先检测 per_cell。`_write_outline_item()` SHALL prefer per_cell over old merge approach.

- 检测 `_block.get("per_cell")` 是否存在
- 存在则调用 `from_dict()` → `write_table_from_data(doc, td)`
- 不存在则走原路径（降级）
- 原 `_write_table_from_lines()` 保留不做修改

#### Scenario: 有 per_cell 时走 write_table_from_data

- per_cell 非空 → 调用 `from_dict()` → `write_table_from_data()`
- per_cell 不存或空 → 调用 `_write_table_from_lines()`（原逻辑不变）

### Requirement: 集成测试验证 round-trip

必须编写测试验证 per-cell format round-trip。Tests SHALL verify write_table_from_data produces correct XML.

- SHALL 构建一个含合并单元格的 TableData
- SHALL 调用 `write_table_from_data()` 写入临时 docx
- SHALL 解析 docx XML 检查 gridSpan/vMerge 元素
- SHALL 检查单元格数量 vs 独立单元格数量（合并后应更少）

#### Scenario: 水平合并生成 gridSpan

- colSpan=3 的单元格在 XML 中包含 `<w:gridSpan w:val="3"/>`
- 被覆盖的列不应有独立单元格

#### Scenario: 垂直合并生成 vMerge

- rowSpan=2 的起始行包含 `<w:vMerge w:val="restart"/>`
- 延续行包含 `<w:vMerge w:val="continue"/>`
