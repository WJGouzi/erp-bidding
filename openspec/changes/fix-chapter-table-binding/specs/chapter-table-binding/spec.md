# 规格：表格-章节绑定 (chapter-table-binding)

## 概述

确保招标文件解析过程中表格与所属章节精确绑定，每张表格出现在其正确的章节位置，不偏移、不重复、不丢失。

## 功能需求

### FR-1: 按章节归属存储表格

- 解析管线提取的每张表格必须与其所在的章节绑定
- 表格数据存储在 `format_requirements.required_sections[i].template_tables` 中
- `format_requirements` 顶层不得包含独立的 `template_tables` 字段
- `analysis_data` 中不得包含独立的 `table_classification` 分类结构

### FR-2: 表格提取范围限制

- `_extract_template_tables` 仅提取当前章节直接内容中的表格
- 不递归到子章节提取（子章节的表格由子章节自己管理）
- 章节内表格和文字段落的出场顺序必须保留

### FR-3: 无默认表格

- 如果招标文件某章节不包含表格，则该章节的 `template_tables` 为空列表
- 不得为没有表格的章节生成默认表格或预设表格
- 技术参数表、产品清单表等只有在原始文档中存在时才被提取

### FR-4: 表格类型不预设

- 表格没有预设的"类型"标签（如 preliminary, product_lists, scoring 等）
- 表格的归属仅由其在文档树中的位置决定
- 不使用关键词匹配来猜测表格类型或重新分配归属

## 非功能需求

### NFR-1: 数据一致性

- required_sections 中不包含重复的表格数据
- 同一个表格不会出现在多个章节中

### NFR-2: 性能

- 表格提取过程不得对整个解析管线产生超过 10% 的额外开销
- 表格定位使用 XML 元素操作而非全文搜索
