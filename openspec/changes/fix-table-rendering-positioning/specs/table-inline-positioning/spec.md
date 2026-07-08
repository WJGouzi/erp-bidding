# Table Inline Positioning

## 概述

确保表格在生成的标书文档中出现在正确的章节位置，而非全部堆叠在文档末尾。

## 当前状态

`write_table_from_data` 使用 `ET.SubElement(doc.element.body, 'tbl')`，将 `<tbl>` 元素直接作为 `body` 的最后一个子元素。无论 python-docx 当前光标在哪，表格始终在文档末尾。

## 目标状态

表格插入到当前章节的段落内容之后，保持与原文一致的「段落→表格→段落」顺序。

## 约束

- 不需要修改 python-docx 库
- 不引入额外的 XML 解析依赖

## 方案

1. `write_table_from_data` 新增 `insert_after` 参数（`Optional[etree.Element]`）
2. 当提供该参数时，使用 `addnext()` 将 `<tbl>` 插入到目标元素之后
3. 调用方（`_build_docx_bytes`）在写入段落时记录最后一个 `_element`，传给表格写入函数
4. 同时更新 `_element_last_pos` 类型的追踪变量

## 边界条件

- 表格前无段落（章节开头立即是表格）：插入到章节标题段落之后
- 连续多个表格：每个表格插入到上一个表格之后
- 空章节（仅有表格无段落）：插入到章节标题之后
