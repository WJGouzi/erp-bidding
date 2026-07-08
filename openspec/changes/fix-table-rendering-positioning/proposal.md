## Why

最新一轮改动（commit 9554d4b）试图修复表格结构和章节绑定，但引入了一系列回归问题，导致：

1. **所有表格堆叠在文档末尾** — `write_table_from_data` 直接用 `doc.element.body.append(tbl)` 追加到 XML body 尾部，无视 python-docx 的插入点
2. **合并单元格完全丢失** — `ContentBlock.merge_cells` property 从 `per_cell_data` 读 `merge_cells` 键，但 `to_dict(TableData)` 不导出该键，始终返回 `[]`
3. **per_cell 键名不匹配** — 模板绑定器 `ContentBlock.to_dict()` 存 `"per_cell"`，而 `_build_docx_bytes` 读 `"per_cell_data"` → 永远走降级路径
4. **to_per_cell 行索引偏移** — `_parse_table` 传入 `rows_data`（不含表头行），但 merge_cells 使用含表头的 0-indexed 行号 → 合并行全部偏移 1
5. **单元格文字 3 倍重复** — 某些表格单元格文字被重复写入 3 次
6. **章节内容丢失** — 资格性响应文件三~七章仅输出 LLM 文本，未保留原文模板结构

这些问题共同导致生成的标准严重不符合招标文件格式要求，属于废标风险。

## What Changes

### 修复项

1. **`table_codec.py` — `write_table_from_data` 表格定位修复**
   - 改为在最近段落 `<p>` 元素之后插入 `<tbl>`，而非追加到 `body` 末尾
   - 维护一个 `_insert_after_element` 参数，允许调用方指定插入位置

2. **`table_codec.py` — `to_dict` 增加 `merge_cells` 导出**
   - 在 `to_dict(TableData)` 的输出中添加 `"merge_cells"` 字段
   - 使 `ContentBlock.merge_cells` property 能正确读取合并信息

3. **`document_parser.py` — `_parse_table` 修复 `to_per_cell` 行索引**
   - 传递完整数据（含表头行）给 `to_per_cell`，修复 merge_cells 行偏移
   - 同时修复缺失的 `merge_cells` 保存到 `per_cell_data`

4. **`document_parser.py` — 修复 `if row_idx == 0` 降级判断**
   - 循环结束后 `row_idx` 指向最后一行 → 用布尔变量追踪是否已成功设置

5. **`helpers.py` — `_build_docx_bytes` 修复 per_cell 键名匹配**
   - 同时检查 `"per_cell"` 和 `"per_cell_data"` 两个键名
   - 优先使用 per_cell 格式，降级到旧三元组格式

6. **`helpers.py` — 模板章节内容回退修复**
   - 当 `content_blocks` 匹配失败时，确保章节内容至少保留标题和占位文本

### 不变的内容

- 不改变 `ContentBlock.__init__` 的字段定义
- 不改变 `analysis_v3` 的 schema 结构
- 不改变 LLM 调用链

## Capabilities

### New Capabilities

- `table-inline-positioning`: 表格插入在正确的位置（当前章节内容中），而非文档末尾
- `merge-cell-full-chain`: 合并单元格信息在解析→存储→全链路保留

### Modified Capabilities

- `table-per-cell-storage`: 增强 `per_cell_data` 的完备性，补充 `merge_cells` 字段

## Impact

- **`table_codec.py`**: `write_table_from_data` 签名变更（新增可选参数），`to_dict` 输出增加 `merge_cells` 键
- **`document_parser.py`**: `_parse_table` 的 `to_per_cell` 调用方式和错误处理修复
- **`helpers.py`**: `_build_docx_bytes` 的 per_cell 键名读取兼容新旧格式
- **向后兼容**: 修改对已有 `per_cell_data` 无破坏性影响（仅增加字段）
