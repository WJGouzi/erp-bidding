## 1. Per-Cell 数据模型

- [x] 1.1 实现 `TableCell` 和 `TableData` 数据类（含 serialization/deserialization）
- [x] 1.2 实现 `to_per_cell()` 转换函数：将当前 `{headers, rows, merges}` 三元组转为 `TableData`
- [x] 1.3 写单元测试：复杂表格的 round-trip（extract → per_cell → JSON → from_json → verify）

## 2. 识别增强

- [x] 2.1 `_extract_raw_table()` 增加列宽提取（从 `<w:tblGrid>/<w:gridCol>` 读取），返回值新增 `column_widths` 字段
- [x] 2.2 `_extract_raw_table()` 增加单元格格式提取（bold, fontName, fontSizeHalfPt, align, vAlign）
- [x] 2.3 写单元测试：验证提取的列宽和格式与原始 docx 一致

## 3. 组装改造

- [x] 3.1 实现 `write_table_from_data(doc, table_data: TableData)` 新入口
- [x] 3.2 `_write_table_from_lines()` 添加 `TableData` 检测和自动路由
- [x] 3.3 写 round-trip 测试：提取 → per_cell → write → 读取验证

## 4. Pipeline 集成

- [x] 4.1 helpers.py 中的表格生成路径统一使用 `TableData` 中间格式
- [x] 4.2 template_binder.py 中 `ContentBlock.table` 适配 `TableData`
- [x] 4.3 跑通完整生成流程：对复杂表格.docx 做一次完整的 extract → store → render
