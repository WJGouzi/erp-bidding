# Tasks: 表格章节绑定修复

## 阶段一：解析阶段修复

- [x] Task 1: 删除 `phase1_5_format.py` 中构建 `all_tables` 的代码（第425-432行）
- [x] Task 2: 删除 `phase1_5_format.py` 返回值中的 `"template_tables": all_tables`（第469行），同时保留 `section_lookup`、`fixed_texts` 等其他字段
- [x] Task 3: 在 `analysis_v3/__init__.py` 中 `assemble_v3_analysis_data` 调用后添加 `analysis_data.pop("table_classification", None)` 防御性清理
- [x] Task 4: 确认 `analysis_v3/__init__.py` 中 `format_requirements` 注入到返回结果时（第762行）不附带顶层 `template_tables`
- [x] Task 5: 修复 `_extract_template_tables` 中 `merge_cells` 的读取路径：优先从 `ContentBlock.per_cell_data` 获取原始合并信息，而非依赖 `_rebuild_merge_cells` 重建

## 阶段二：生成阶段修复

- [x] Task 6: 在 `helpers.py` 的 `_write_outline_item` 外层引入全局 `_last_elements` 列表追踪
- [x] Task 7: 修改 `_write_outline_item` 中 ContentBlock 渲染路径，每次写入段落或表格后追加到 `_last_elements` 并以此为参考定位
- [x] Task 8: 修改 `_write_outline_item` 中 JSON table marker 渲染路径，同样使用 `_last_elements` 追踪而非局部变量
- [x] Task 9: 确保所有子章节递归调用共享外层 `_last_elements`（使用闭包或 nonlocal）

## 阶段三：表格写入与合并修复

- [x] Task 10: 在 `write_table_from_data` 中增加 `insert_after` 元素的有效性检查：确保它是 body 的直接子代（`w:p` 或 `w:tbl`），否则回溯找到最近的 body 子代
- [x] Task 11: 验证 `to_dict` → `_rebuild_merge_cells` 在复杂合并场景下不丢失合并信息，必要时修复重建逻辑
- [x] Task 12: 在 `table_classifier.py` 顶部添加明确的 DEPRECATED 标记和移除说明

## 阶段四：消费端验证

- [x] Task 13: 确认 `helpers.py` 中所有 `format_requirements` 引用均正确使用 `required_sections[].template_tables` 而非顶层字段
- [x] Task 14: 确认 `technical.py` 和 `business.py` 读取 `template_tables` 路径正确
- [x] Task 15: 确认 `_generate_table_content` 的章节匹配逻辑在修复后仍能精确定位

## 阶段五：测试与验证

- [x] Task 16: 用真实招标文件运行完整管线，确认 `analysis_data` JSON 不含 `table_classification` 和顶层 `template_tables`
- [x] Task 17: 用真实招标文件运行完整管线，确认 `format_requirements.required_sections[].template_tables` 包含正确的表格数据
- [x] Task 18: 生成标书 DOCX，验证表格出现在正确的章节位置（不是文档末尾）
- [x] Task 19: 验证合并单元格在生成的 DOCX 中正确渲染（水平和垂直合并）
- [x] Task 20: 验证不包含表格的章节不会生成空表或默认表
- [x] Task 21: 运行现有单元测试，确认不引入回归

**总计：21 个任务**
