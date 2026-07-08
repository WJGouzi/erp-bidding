## 1. 清理 phase1_5_format.py 中的 template_tables 提取逻辑

- [x] 1.1 删除 `_extract_template_tables()` 函数
- [x] 1.2 删除 `_collapse_merged_columns()` 函数（仅被 `_extract_template_tables` 使用）
- [x] 1.3 保留 `_build_per_cell()`（template_content 的 table 块仍需作为 fallback 使用）；删 `_collapse_merged_columns()`
- [x] 1.4 从 `_extract_required_sections()` 中移除 `template_tables` 字段的构建
- [x] 1.5 删除 `extract_format_requirements()` 中的 `template_tables` 统计日志
- [x] 1.6 删除 `analysis_schema.py` 中的 `TemplateTable` data class 和 `template_tables` 字段定义
- [x] 1.7 确保 `template_content` 中的 table 块包含 `headers`、`rows` 等扁平字段供消费方直接使用

## 2. 迁移消费方从 template_tables 改为 template_content

- [x] 2.1 修改 `technical.py` 中读取 `template_tables` 的地方，改为从 `template_content` 过滤 `type == "table"` 的块
- [x] 2.2 修改 `business.py` 中读取 `template_tables` 的地方，改为从 `template_content` 过滤 `type == "table"` 的块
- [x] 2.3 修改 `helpers.py` 中所有 `template_tables` 引用（约4处），改为从 `template_content` 过滤表格块
- [x] 2.4 验证 `template_binder.py` 的 `build_template()` 已正确从 `template_content` 读取（如无问题则跳过）

## 3. 修复文档解析器中 ContentBlock.per_cell_data 在 to_dict 中的序列化

- [x] 3.1 确认 `ContentBlock.to_dict()` 中当有 `per_cell_data` 时，同时输出 `headers`/`rows`/`merge_cells` 兼容字段
- [x] 3.2 确认 `ContentBlock.from_dict()` 反向构造时能正确读取

## 4. DOCX 表格边框渲染为黑色实线

- [x] 4.1 在 `write_table_from_data()` 的 `tblPr` 中增加 `tblBorders` 子元素，设置四条边框为单线黑色
- [x] 4.2 在 `helpers.py` 中 `document.add_table()` + `_t.style = "Table Grid"` 的渲染处（约 line 3732），同样增加黑色实线边框

## 5. 测试与验证

- [x] 5.1 运行现有测试套件，确保无回归
- [x] 5.2 更新 `test_template_binder.py` 中可能引用 `template_tables` 的测试用例
- [x] 5.3 验证生成的 DOCX 中表格边框为黑色实线

## 6. 修复渲染顺序问题

- [x] 6.1 `_write_outline_item` 中渲染文本块时兼容 `"text"` 和 `"paragraph"` 两种类型
- [x] 6.2 分隔页子节点渲染中，`_write_outline_item` 回退后更新 `_last_child_element`
