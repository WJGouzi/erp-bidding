# fix-chapter-table-binding

修复表格章节绑定和定位问题：1) 删除 format_requirements 顶层 template_tables，表格仅存在 required_sections[].template_tables 2) 修复 _write_outline_item 中 _last_element 追踪失效导致表格全部堆在文档末尾 3) 确保 analysis_data 不包含 table_classification 4) 修复表格合并单元格渲染
