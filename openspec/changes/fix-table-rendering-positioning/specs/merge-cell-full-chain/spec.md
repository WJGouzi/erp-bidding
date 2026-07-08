# Merge Cell Full Chain Preservation

## 概述

确保表格合并单元格信息在「解析 → 存储 → 提取 → 模板绑定 → 渲染」全链路中完整保留。

## 当前状态

合并信息在多个环节丢失：
1. `to_dict(TableData)` 不导出 `merge_cells`
2. `ContentBlock.merge_cells` property 从 `per_cell_data` 读不到
3. `_build_per_cell(phase1_5_format)` 接收空 merge_cells
4. template_binder 存 `"per_cell"` 但 helpers 读 `"per_cell_data"`
5. `_parse_table` 的 `to_per_cell` 行索引偏移 1

## 目标状态

招标文件中任意复杂度的合并单元格（水平合并 + 垂直合并 + 混合合并）在生成的标书中完全保留。

## 约束

- 不改变 `TableData` 数据类的字段结构
- 不改变 `TableCell.col_span` / `row_span` 语义（已正确编码合并信息）

## 方案

### 第一层：to_dict 导出 merge_cells

从 `TableData.rows` 中重建 `merge_cells`：

```
for ri, row in enumerate(td.rows):
    ci = 0
    for cell in row.cells:
        if cell.hidden: ci += 1; continue
        if cell.col_span > 1:
            merges.append({"type":"horizontal","row":ri,"col":ci,"span":cell.col_span})
        if cell.row_span > 1:
            merges.append({"type":"vertical","row":ri,"col":ci,"span":cell.row_span})
        ci += cell.col_span
```

### 第二层：ContentBlock 属性修复

`merge_cells` property 优先从 `per_cell_data["merge_cells"]` 读取，不存在时才返回 `[]`。

### 第三层：存储路径统一

`_parse_table` 中将 `merge_cells` 显式写入 `per_cell_data["merge_cells"]`。

### 第四层：读取键名兼容

`helpers.py` 同时检查 `"per_cell"` 和 `"per_cell_data"` 两个键名。

## 验证

用以下结构的招标文件验证：
- 第1行跨列合并（gridSpan）
- 第1列跨行合并（vMerge restart/continue）
- 混合合并（同时有水平和垂直）
- 不规则合并（同一行多组合并）
