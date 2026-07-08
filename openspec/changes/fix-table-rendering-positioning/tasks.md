## Tasks

### T1: table_codec — to_dict 增加 merge_cells 导出

- [x] T1.1 In `to_dict(TableData)`, iterate `td.rows` cell attributes (col_span > 1 / row_span > 1), rebuild `merge_cells` list and add to output dict
- [x] T1.2 Verify `from_dict(to_dict(td)).rows` matches `td.rows` cell span attributes

### T2: table_codec — write_table_from_data 支持插入位置

- [x] T2.1 Add optional parameter `insert_after: Optional[etree.Element] = None` to `write_table_from_data`
- [x] T2.2 When provided, use `insert_after.addnext(tbl)` instead of `body.append(tbl)`
- [x] T2.3 When not provided, keep original behavior (body.append)

### T3: document_parser — 修复 to_per_cell 行索引偏移

- [x] T3.1 In `_parse_table`, change `to_per_cell` call to pass `all_rows = [_header_cells] + rows_data` (including header row)
- [x] T3.2 Verify merge cell information correctly aligns to table rows

### T4: document_parser — 修复合并信息保存到 per_cell_data

- [x] T4.1 After `to_per_cell` success, write `merge_cells` into `per_cell_data["merge_cells"]`
- [x] T4.2 Verify `ContentBlock(per_cell_data=...).merge_cells` returns correct merge info

### T5: document_parser — 修复 row_idx 降级判断

- [x] T5.1 Replace `if row_idx == 0` with boolean variable `_per_cell_built = False`, set to True in try block
- [x] T5.2 On exception, `per_cell_data` correctly becomes None

### T6: phase1_5_format — 修复 merge_cells 提取

- [x] T6.1 In `_extract_required_sections`, change `template_content` per_cell to use `block.per_cell_data` directly instead of `_build_per_cell`
- [x] T6.2 Verify template_content per_cell contains merge information

### T7: helpers — 修复 per_cell 键名匹配

- [x] T7.1 In `_build_docx_bytes`, change per_cell_data reading to check both `"per_cell"` and `"per_cell_data"` keys
- [x] T7.2 Verify template-bound table content_blocks correctly read per_cell data

### T8: helpers — 表格插入位置追踪

- [x] T8.1 In `_write_outline_item`, track the last paragraph XML element reference
- [x] T8.2 Pass it to `write_table_from_data` as `insert_after` parameter
- [x] T8.3 Verify tables appear in their chapter content, not at document end

### T9: template_binder — to_dict 双写 per_cell 键名

- [x] T9.1 In `template_binder.ContentBlock.to_dict()`, write both `"per_cell"` and `"per_cell_data"` keys
- [x] T9.2 Verify both old and new reading paths can access per_cell data

### T10: 验证

- [x] T10.1 Run full pipeline with a tender document containing complex merged cells
- [x] T10.2 Check: tables at correct chapter positions, merge cells preserved, no text duplication, complete chapter structure
