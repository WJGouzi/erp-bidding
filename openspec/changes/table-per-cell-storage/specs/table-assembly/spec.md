# 表格组装改造

## 概述

将 `_write_table_from_lines()` 从「文本行+可选 merges」模式改造为直接接收 `TableData` 对象，按 per-cell 属性生成 docx XML。

## 接口

### 主要入口

```python
def write_table_from_data(doc, table_data: TableData) -> None
```

接收 `TableData` 直接写 XML：
1. 从 `table_data.gridCols` 生成 `<w:tblGrid>` 列定义
2. 逐行遍历 `table_data.rows`，跳过 `hidden=True` 的单元格
3. 有 `colSpan>1` 的设置 `<w:gridSpan>`
4. 标记 `rowSpan` 起始的为 `vMerge="restart"`，延续的为 `vMerge="continue"`
5. 按单元格属性设置字体 run 格式

### 向后兼容

```python
def _write_table_from_lines(doc, lines, merges=None) -> None
```

检测增强：如果输入是 `TableData` 走新路径，否则走旧路径（作为降级）。

## XML 生成规则

### 列宽

```xml
<w:tblGrid>
  <w:gridCol w:w="1200"/>
  <w:gridCol w:w="1800"/>
  <w:gridCol w:w="1200"/>
</w:tblGrid>
```

### 合并单元格

```xml
<!-- 水平合并：colSpan=3 -->
<w:tcPr>
  <w:tcW w:w="4200" w:type="dxa"/>
  <w:gridSpan w:val="3"/>
</w:tcPr>

<!-- 垂直合并起始 -->
<w:tcPr>
  <w:vMerge w:val="restart"/>
</w:tcPr>

<!-- 垂直合并延续 -->
<w:tcPr>
  <w:vMerge w:val="continue"/>
</w:tcPr>
```

### 格式

```xml
<w:rPr>
  <w:rFonts w:ascii="仿宋" w:hAnsi="仿宋" w:eastAsia="仿宋"/>
  <w:b/>                           <!-- 加粗 -->
  <w:sz w:val="24"/>               <!-- 字号（half-pt） -->
  <w:szCs w:val="24"/>
</w:rPr>
```

## 降级策略

当 `lines` 参数是纯文本列表时，走旧逻辑（均分列宽、无格式）。
当检测到 `lines` 是 `TableData` 对象时，走新路径。
