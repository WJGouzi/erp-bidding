# 表格识别增强

## 概述

在现有的 `_extract_raw_table()` 基础上，增加列宽和单元格格式的提取。不改变现有合并检测逻辑，只做增量增强。

## 提取内容

### 列宽

从 `<w:tblGrid>/<w:gridCol w="xxx">` 提取每列宽度。

- 位置：`_extract_raw_table()` 返回值新增 `column_widths: list[int]`
- 默认值：`[]`（表示由渲染端自动计算）

### 单元格格式

从单元格的 XML 节点提取：

| 属性 | XML 路径 | 取值 |
|------|----------|------|
| bold | `w:tc/w:p/w:r/w:rPr/w:b` | 存在=true |
| fontName | `w:tc/w:p/w:r/w:rPr/w:rFonts @w:eastAsia` | 属性值 |
| fontSizeHalfPt | `w:tc/w:p/w:r/w:rPr/w:sz @w:val` | 整数值 |
| align | `w:tc/w:p/w:pPr/w:jc @w:val` | 属性值 |
| vAlign | `w:tc/w:tcPr/w:vAlign @w:val` | 属性值 |

只取单元格中第一个段落的第一个 run 的格式。

### 转换入口

新增 `to_per_cell(headers, rows, merges, column_widths=None) → TableData`

将当前的三元组格式（headers + rows + merges）与可选的 column_widths 合并为统一的 `TableData` 对象。

## 向后兼容

`_extract_raw_table()` 的现有返回值结构不变，只增加 `column_widths` 字段。
新的格式转换走新增函数，不修改现有数据流。
