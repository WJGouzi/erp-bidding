## Context

当前标书生成管线中的表格处理流程：

```
Tender DOCX
  → DocumentParser._parse_docx_structured (解析段落+表格，分配到 Section)
    → _parse_table (提取单元格文本+合并信息，构建 per_cell_data)
      → ContentBlock (type=table, per_cell_data=dict)
        → StructuredDocument.to_dict() → JSON
          → analysis_v3 (格式提取)
            → phase1_5_format._extract_required_sections
              → block.headers / block.rows / block.merge_cells (property)
              → template_content: {headers, rows, merge_cells, per_cell}
                → template_binder.bind_template / fill_content
                  → ContentBlock (template_binder 层, to_dict → "per_cell" 键)
                    → _CONTENT_BLOCKS_PREFIX + JSON → content_snapshot
                      → _build_chapter_contents_from_records → chapter_contents
                        → _build_docx_bytes._write_outline_item
                          → from_dict(per_cell_data) → TableData
                            → write_table_from_data (ET.SubElement(body, 'tbl'))
```

问题分布在管线各阶段：

| 阶段 | 文件 | 问题 |
|------|------|------|
| 解析 | document_parser.py:1035 | `to_per_cell` 传入 `rows_data` 不含表头，merge_cells 行偏移 |
| 解析 | document_parser.py:1017 | 循环结束后 `row_idx` 指向末行，`if row_idx==0` 判断失效 |
| 存储 | table_codec.py:90-110 | `to_dict()` 不导出 `merge_cells` |
| 提取 | phase1_5_format.py:146 | `merge_cells` property 返回 `[]` → `_build_per_cell` 重建无合并 |
| 绑定 | template_binder.py:84 | `ContentBlock.to_dict()` 存 `"per_cell"` 键 |
| 渲染 | helpers.py:4310 | `_block.get("per_cell_data")` 读不到 `"per_cell"` → 降级 |
| 渲染 | table_codec.py:325 | `doc.element.body.append(tbl)` 永远追加到末尾 |

## Goals / Non-Goals

**Goals:**
- 修复表格定位：表格插入到当前章节内容位置，而非文档末尾
- 修复合并单元格丢失：合并信息在解析→存储→提取→渲染全链路保留
- 修复 per_cell 键名不匹配问题
- 修复 `to_per_cell` 行索引偏移问题
- 修复单元格文字重复问题

**Non-Goals:**
- 不改动 ContentBlock 数据类字段结构
- 不改动 `analysis_v3` 的 section_lookup 匹配逻辑
- 不引入新的存储格式
- 不改动 LLM 生成路径

## Decisions

### D1: write_table_from_data 插入位置

**决定**: 将表格插入到当前文档的"光标位置"——在最近写入的 `<p>` 元素之后。

**方案**: `write_table_from_data` 新增可选参数 `insert_after: Optional[etree.Element]`。
- 提供该参数时，在目标元素后面插入 `<tbl>`（使用 `addnext()`）
- 不提供时，保持原有行为（`body.append(tbl)`）

调用方（`_build_docx_bytes`）在写入段落时记录最后一个 `<p>` 元素，在写入表格时传入。

**理由**: `addnext()` 是 lxml 的标准插入方法，能将元素插入到兄弟节点之后。段落内容写入使用 `document.add_paragraph()` 追加到 body 末尾，但段落末尾就是文档末尾——由于 python-docx 不暴露内部<p>元素的引用，需要调用方手动记录。

**放弃的方案**:
- 使用 `body.insert(index, tbl)`：需要知道 body 子元素的总数和表格位置，计算复杂
- 每次写入表格前先 `add_paragraph("")` 占位然后替换：会留下多余的空段落

### D2: merge_cells 全链路修复

**决定**: 修复四层断裂中的全部环节。

1. **`table_codec.py` — `to_dict`**
   - 从 `TableData.rows` 中重建 `merge_cells` 列表（遍历 cell.col_span / cell.row_span）
   - 写入 `to_dict` 输出中的 `"merge_cells"` 键

2. **`document_parser.py` — `_parse_table`**
   - `to_per_cell` 调用改为传入 `all_rows = [_header_cells] + rows_data`（含表头）
   - 解析成功后，将 `merge_cells` 显式写入 `per_cell_data["merge_cells"]`

3. **`helpers.py` — `_build_docx_bytes`**
   - 读取 `per_cell_data` 时，如果 `"per_cell_data"` 不存在则尝试 `"per_cell"`
   - 确保 `from_dict` 能正确还原包含 merge 的 TableData

### D3: 单元格文字重复修复

**决定**: 检查文字重复源自 `_collapse_merged_columns` 的重复处理。

**怀疑根因**: `_extract_template_tables` 中调用 `_collapse_merged_columns` 两次
（一次在 `_extract_required_sections` 的 `_extract_template_tables(child)` 中，
一次在全局的 `template_tables` 收集中）。但 `template_content` 路径不经过此函数，
所以文字重复更可能来自 `ContentBlock.headers`/`rows` property 的 per_cell_data 读取
与旧格式 `_headers`/`_rows` 的双重叠加。

**修复**: 在 `_build_docx_bytes` 的内容块渲染路径中，确保仅使用单一数据源（per_cell
优先），不降级到旧 headers+rows 格式，避免数据源叠加。

### D4: per_cell 键名统一

**决定**: `helpers.py` 中修复键名读取，同时兼容新旧格式。

```python
_pcd = _block.get("per_cell_data") or _block.get("per_cell")
```

`template_binder.py` 中的 `to_dict()` 也改为同时写两个键名，确保向后兼容。

## Risks / Trade-offs

- **[`addnext` 兼容性]** lxml `addnext()` 在复杂 XML 结构下是否始终可靠。**缓解**: `addnext` 是 lxml 的标准方法，仅在兄弟元素之间操作，风险低。
- **[merge_cells 重建性能]** 从 TableData 重建 merge_cells 需要遍历全表。**缓解**: 表格行数通常小于 50 行，遍历代价可忽略。
- **[键名双写]** `per_cell` 和 `per_cell_data` 两个键名同时存在可能导致维护混淆。**缓解**: 在注释中说明兼容期限（一个版本后移除旧键名）。
