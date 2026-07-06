# Per-Cell 自描述表格数据模型

## 概述

统一表格数据在「识别→存储→组装」三阶段中的表示形式。每个单元格独立描述，携带合并跨度、格式、位置等属性。

## 数据结构

### TableCell

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `text` | string | `""` | 单元格文本内容 |
| `colSpan` | int | `1` | 水平合并跨度，1=不合并 |
| `rowSpan` | int | `1` | 垂直合并跨度，1=不合并 |
| `hidden` | bool | `false` | 被合并覆盖的虚拟单元格，渲染时跳过 |
| `bold` | bool | `false` | 是否加粗 |
| `fontName` | string | `""` | 字体名，空=使用默认 |
| `fontSizeHalfPt` | int | `0` | 字号（half-points），0=使用默认 |
| `align` | string | `""` | 水平对齐: left/center/right |
| `vAlign` | string | `""` | 垂直对齐: top/center/bottom |

### TableData

| 字段 | 类型 | 说明 |
|------|------|------|
| `gridCols` | int[] | 每列宽度（twips） |
| `tableWidth` | int | 表格总宽（twips），默认 9072 |
| `rows` | TableRow[] | 行列表 |
| `borders` | bool | 是否有边框，默认 true |

### TableRow

| 字段 | 类型 | 说明 |
|------|------|------|
| `cells` | TableCell[] | 该行所有单元格（含 hidden=true 的虚拟格） |
| `height` | int | 行高（twips），0=自动 |

## JSON 序列化

字段名使用 camelCase（与前端/API 约定一致）。

隐藏单元格必须保留在数组中以保证列索引对齐。

## 约束

- `gridCols.length` 必须等于每行 `cells.length`
- `hidden: true` 的单元格 `colSpan` 和 `rowSpan` 必须为 1
- `colSpan` 不能导致跨出 `gridCols` 边界
- `rowSpan` 不能跨出 `rows` 边界
