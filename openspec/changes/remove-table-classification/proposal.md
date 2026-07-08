## Why

当前的表格处理管线存在两个架构性问题：

### 问题一：table_classification 冗余且有害

`analysis_v3/__init__.py` 在解析完成后调用 `classify_all_tables(doc.tables)`，将文档中所有表格从章节结构中"抠"出来，拍平成按类型分类的独立结构。

这导致：
1. **表格脱离章节上下文** — `raw_tables` 是一个拍平列表，不知道每个表格属于哪个章节
2. **周边文字丢失** — 表格前后的说明段落没有保留
3. **双重数据源** — `format_requirements.required_sections[].template_content[]` 已经按章节存储了表格，`table_classification` 又存一份
4. **重新猜章节** — 生成时 `_match_raw_table()` 用关键词猜测表格归属，错误率高
5. **默认模板造假** — 猜不到表格时，`_generate_table_content` 创建默认表格，这些在招标文件中并不存在

### 问题二：format_requirements 提取链路断裂

即使 `table_classification` 移除后消费者改为从 `required_sections` 读取，当前 `format_requirements` 的提取本身就有 bug：
- `merge_cells` property 从 `per_cell_data` 读不到 → `template_content` 中的 table 缺少合并信息
- `_build_per_cell` 重建 per_cell 时 merge_cells 为空
- 模板绑定器 `to_dict()` 存 `"per_cell"` 但读者读 `"per_cell_data"` → 匹配失败

这些问题已经在 `fix-table-rendering-positioning` 中修复。

## What Changes

### 1. 移除 table_classification

- 删除 `analysis_v3/__init__.py` 中对 `classify_all_tables()` 的调用
- `table_classifier.py` 标记废弃（保留文件，删除所有调用点）
- 从 `analysis_data` schema 中移除 `table_classification` 字段

### 2. 消费者改为从 format_requirements 读取

所有原本读 `table_classification` 的代码改为遍历 `format_requirements.required_sections`，按 `file_type` 筛选：

| 文件 | 原读取 | 改为 |
|------|--------|------|
| `technical.py` `_collect_from_tech_tables` | `tc.tech_requirements` | `required_sections` 中 `file_type=technical` 的 `template_content` 里的 table |
| `technical.py` `_collect_from_product_lists` | `tc.product_lists` | `file_type=price_list` 的 table |
| `business.py` `_collect_from_biz_*` | `tc.business/service_requirements` | `file_type=business/service` 的 table |
| `schemas.py` `_convert_table_classification_scoring` | `tc.scoring` | `file_type=scoring_response` 的 table |
| `helpers.py` `_extract_analysis_context` | `tc.product_lists` / `tc.raw_tables` | `required_sections` 中取 |

### 3. 移除生成阶段的降级逻辑

- 删除 `_match_raw_table()` — 不再需要关键词猜章节
- 删除 `_generate_table_content` 中的默认表格创建（无匹配时创建默认行）
- 确保生成时只使用 `template_content`（含 ContentBlock path）中的表格

## Capabilities

### New
- `chapter-scoped-tables`: 表格数据仅存在于章节结构中，不拍平不分类
- `no-fallback-extraction`: 提取错误时不降级，暴露问题

### Removed
- `table-classification`: 不再需要独立的表格分类结构

## Impact

- 删除约 200 行 `classify_all_tables` 和 `_match_raw_table` 代码
- 修改约 5 个消费者文件
- `analysis_data` JSON 体积减少（移除 `table_classification` 字段）
- 生成的标书不会再有招标文件中不存在的默认表格
