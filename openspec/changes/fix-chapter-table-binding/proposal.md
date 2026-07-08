## Why

当前表格提取和标书生成中存在两个核心问题：1) `format_requirements` 中表格存储结构存在冗余和错位，`table_classification` 独立分类方案导致表格与章节脱离；2) 生成的标书中表格全部堆在文档末尾而非对应的章节位置。这两者导致标书内容错乱，无法通过评审。

## What Changes

- **BREAKING**: 删除 `format_requirements.template_tables` 顶层字段，表格仅存储在 `required_sections[].template_tables` 中
- **BREAKING**: 删除 `analysis_data.table_classification` 废弃结构，表格归属由章节驱动而非预设分类
- 修复 `_write_outline_item` 中 `_last_element` 追踪失效导致表格定位到文档末尾的 bug
- 修复 `_extract_template_tables` 对合并单元格数据的正确传递
- 修复 `write_table_from_data` 中 `insert_after` 在文档元素树中的准确定位
- 删除 `table_classifier.py` 中不再使用的 `classify_all_tables` 和 `extract_table_surroundings`
- 确保 `format_requirements` 提取时严格执行"文档有什么表就绑什么表"原则

## Capabilities

### New Capabilities
- `chapter-table-binding`: 表格与章节的精确绑定能力，确保每张表格归属到其所在章节的 `template_tables`，不偏移、不重复
- `table-positioning`: 表格在生成标书文档中的精确定位能力，表格出现在对应的章节标题和文本之后

### Modified Capabilities
无。这是对现有表格系统的修复，不涉及新功能需求变更。

## Impact

- `phase1_5_format.py`: 删除顶层 `template_tables` 字段
- `analysis_v3/__init__.py`: 确认不注入 `table_classification`，确认 `format_requirements` 仅通过 `required_sections` 传递表
- `helpers.py`: 修复 `_write_outline_item` 中的 `_last_element` 追踪；所有 `format_requirements.template_tables` 引用改为 `required_sections[].template_tables`
- `table_codec.py`: 修复 `write_table_from_data` 的 `insert_after` 定位
- `technical.py`, `business.py`: 确认消费端读取 `template_tables` 路径正确
- `table_classifier.py`: 标记为 DEPRECATED，清理调用方
