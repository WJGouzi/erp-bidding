## Context

当前表格数据在管道中走 3 条并行路径，各自保留不同的信息子集：

| 路径 | headers | rows | merges | 列宽 | 格式 | 使用场景 |
|------|:------:|:----:|:-----:|:---:|:---:|---------|
| ContentBlock.to_dict() | ✓ | ✓ | ✓ | ✓ | ✗ | 章节内容存储 |
| raw_tables (classifier) | ✓ | ✓ | ✓ | ✗ | ✗ | 产品表/通用表 |
| 纯文本 tab 分隔 | ✓ | ✓ | ✗ | ✗ | ✗ | 生成环节回退路径 |

组装阶段 `_write_table_from_lines()` 将所有列宽均分为 `9072 // max_cols`，且有一条回退路径完全不带 merge 信息调用 (`merges=[]`)，导致复杂表格的合并结构在生成时丢失。

## Goals / Non-Goals

**Goals:**
- 统一的 per-cell 自描述存储格式覆盖所有表格路径
- 识别阶段提取列宽和单元格级格式（字体、加粗、对齐）
- 组装阶段从 per-cell 属性直接生成 docx XML，还原原始结构和格式
- 现有合并检测逻辑（gridSpan/vMerge 去重）保持不变
- 所有现有测试通过

**Non-Goals:**
- 不做嵌套表格解析（当前无此需求）
- 不做图片/嵌入式对象在表格单元格中的处理
- 不改变表格分类策略（`classify_table()` 逻辑不动）
- 不引入新的外部依赖

## Decisions

### Decision 1: Per-Cell 自描述格式

每个单元格是一个独立的对象，携带全部属性。合并单元格的信息编码在起始单元格的 `colSpan`/`rowSpan` 中，被覆盖的虚拟单元格标记 `hidden: true`。

```python
@dataclass
class TableCell:
    text: str = ""
    col_span: int = 1          # 水平合并跨度（1=不合并）
    row_span: int = 1          # 垂直合并跨度（1=不合并）
    hidden: bool = False       # 被合并覆盖的虚拟单元格
    bold: bool = False
    font_name: str = ""
    font_size_half_pt: int = 0  # half-points，0=未指定
    align: str = ""            # left/center/right
    v_align: str = ""          # top/center/bottom

@dataclass
class TableData:
    grid_cols: list[int]      # 每列宽度（twips）
    rows: list[list[TableCell]]
    table_width: int = 9072   # 表格总宽（twips）
    borders: bool = True
```

**序列化为 JSON：**
```json
{
  "gridCols": [1200, 1800, 1200, 1800, 1200, 1200, 1800, 1200, 1200, 1200, 800, 800],
  "tableWidth": 9072,
  "rows": [
    {
      "cells": [
        {"text": "供应商名称", "colSpan": 11, "bold": true},
        {"hidden": true}, {"hidden": true}, {"hidden": true},
        {"hidden": true}, {"hidden": true}, {"hidden": true},
        {"hidden": true}, {"hidden": true}, {"hidden": true},
        {"hidden": true}, {"hidden": true}
      ]
    },
    {
      "cells": [
        {"text": "注册地址", "colSpan": 6},
        {"hidden": true}, {"hidden": true}, {"hidden": true},
        {"hidden": true}, {"hidden": true}, {"hidden": true},
        {"text": "邮政编码", "colSpan": 3},
        {"hidden": true}, {"hidden": true},
        {"hidden": true}, {"hidden": true}
      ]
    }
  ]
}
```

**Rationale vs 当前方案（分离的 merges 数组）：**
- 每行独立，插入/删除行不影响其他行的索引
- 列宽直接绑定在 `gridCols` 上而不是硬编码均分
- 空格子明确标记 `hidden: true` 而非歧义的 `""`
- 无需查 merge 索引，每格自带信息
- 序列化/反序列化简单直接

### Decision 2: 统一入口 `TableCodec` 编解码器

新增一个模块负责三种转换：

```
_extract_raw_table()  →  to_per_cell()  →  TableData
                                               │
                                               ├─ to_dict() → JSON（存储）
                                               │
                                               ├─ to_xml(doc) → docx XML（组装）
                                               │
                                               └─ from_dict() ← JSON（反序列化）
```

### Decision 3: 格式提取只做「可保留的」

当前提取只提取单元格的纯文本。增强后提取：
- **列宽**：从 `<tblGrid>/<gridCol w="...">` 读取
- **加粗**：单元格内首个 run 的 `<b>` 元素
- **字体**：单元格内首个 run 的 `<rFonts>` 元素
- **字号**：单元格内首个 run 的 `<sz>` 元素
- **对齐**：段落的 `<jc>` 元素
- **垂直对齐**：单元格的 `<vAlign>` 元素

不提取（复杂度高但收益低）：
- 单元格内多段落的各自格式（只取首段落格式）
- 边框样式（统一使用 Table Grid）
- 底纹/颜色

### Decision 4: 向后兼容

- `_extract_raw_table()` 返回值增加 `column_widths` 字段，原有字段不动
- 新增 `to_per_cell_format()` 函数从 `{headers, rows, merges}` 转换为 `TableData`
- `_write_table_from_lines()` 增加重载：检测到输入是 `TableData` 对象时走新路径
- 旧路径（纯文本+merges）保留作为降级

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 存储格式增大（每个单元格携带完整属性） | JSON 压缩后增量可接受：15×12 表格约 3-5KB |
| 现有存储数据与新格式不兼容 | 向后兼容设计，旧格式自动转换为 per-cell |
| 格式提取增加解析耗时 | 只在首次解析时提取，缓存后复用 |
| ContentBlock 已有 merge_cells/column_widths 字段但未充分使用 | 增强这些字段的填充逻辑，而非新建结构 |

## Migration Plan

1. 实现 `TableData` / `TableCell` 数据类和 `TableCodec` 编解码器
2. 在 `_extract_raw_table()` 中增加列宽和格式提取（不影响现有返回结构）
3. 写单元测试：复杂表格的 extract → per_cell → to_xml → verify round-trip
4. 改造 `_write_table_from_lines()` 使其接收 `TableData`
5. 统一管道中各路径使用 `TableData` 作为中间格式
6. 跑全部现有测试，确保回归安全

## Open Questions

- ContentBlock 的表格字段是否直接替换为 TableData，还是保持兼容？
- 垂直合并的"其中"跨 5 行场景的格式提取是否需要每行独立？
