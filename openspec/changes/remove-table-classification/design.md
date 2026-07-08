## Context

### 当前数据流（变更后）

```
解析阶段
  DocumentParser._parse_docx_structured
    → Sections[].content[] = [p1, TABLE-ct1, p2, p3, TABLE-ct2, ...]
    → 每个 ContentBlock 有 per_cell_data（含 merge_cells）

分析阶段
  analysis_v3/__init__.py:start_analyze_v3
    └── phase1_5_format.extract_format_requirements(doc.sections)
          → format_requirements
              └── required_sections[]
                    ├── title: "第四章 技术规格"
                    ├── file_type: "technical"         ← 文件类型（price_list/scoring/等）
                    ├── template_content: [...]        ← 段落+表格交替（渲染用）
                    └── template_tables: [...]         ← 纯表格（提取用）
                          [{headers, rows, merge_cells}, ...]

消费者读取方式：
  technical.py       → sec["template_tables"]  （file_type=technical）
  business.py        → sec["template_tables"]  （file_type=business/service）
  _generate_table_content → sec["template_tables"]（按章节标题匹配）
```

## Goals

- 表格数据仅存在于章节结构中，不拍平不分类
- 所有消费者从 `required_sections[].template_tables` 读取
- 生成标书时不再用关键词猜章节归属
- 招标文件没有的表格不在标书中生成（无默认表降级）

## 变更清单

### 1. table_codec.py — 修复表格渲染

| 改动 | 说明 |
|------|------|
| `to_dict()` 增加 `merge_cells` 导出 | 从 `TableData.rows` 的 cell span 重建 merge_cells，写入输出 dict |
| `write_table_from_data()` 新增 `insert_after` 参数 | 可选，在指定 XML 元素之后插入 `<tbl>` |
| `write_table_from_data()` 返回 `tbl` 元素 | 调用方可以追踪表格位置 |

### 2. document_parser.py — 修复表格解析

| 改动 | 说明 |
|------|------|
| `_parse_table()` 中 `to_per_cell` 传入含表头的完整行 | `all_rows = [_header_cells] + rows_data`，修复 merge_cells 行偏移 |
| `merge_cells` 显式写入 `per_cell_data["merge_cells"]` | 确保下游 `ContentBlock.merge_cells` 能读到 |
| except 块改用 `_per_cell_built` 布尔变量 | 修复 `row_idx == 0` 判断失效（循环结束 row_idx 指向末行） |

### 3. template_binder.py — 修复 per_cell 键名

| 改动 | 说明 |
|------|------|
| `ContentBlock.to_dict()` 同时写 `"per_cell"` 和 `"per_cell_data"` | 兼容新旧读路径 |

### 4. phase1_5_format.py — 修复 merge_cells 提取

| 改动 | 说明 |
|------|------|
| `_extract_required_sections` 中 per_cell 优先使用 `block.per_cell_data` | 不再通过 _build_per_cell 重建（merge_cells 为空） |

### 5. helpers.py — 移除 table_classification 消费 + 修复渲染

| 改动 | 说明 |
|------|------|
| `_extract_analysis_context` 移除 `tc.raw_tables`→`_raw_product_tables` | 不再从 table_classification 提取全局表格列表 |
| `_extract_analysis_context` 保留 `_format_requirements` | 供下游按章节查找 |
| `_generate_table_content` 改为读取 `sec["template_tables"]` | 按章节标题匹配，不调用 `_match_raw_table` |
| `_generate_table_content` 移除默认表格创建 | 无匹配时返回空字符串 |
| 删除 `_match_raw_table()` | 关键词猜章节逻辑，约 80 行 |
| 删除 `_fill_table_from_original()` | 不再需要 |
| 删除 `_detect_table_columns()` | 不再需要 |
| 删除 `_extract_table_data_from_analysis()` | 不再需要 |
| 删除 `_normalize_row_heights()` | 不再需要 |
| `_write_outline_item` 中追踪 `_last_element` | 段落/表格写入后记录 XML 元素引用，传给 `write_table_from_data` |
| `_build_docx_bytes` 中 per_cell 键名兼容 `"per_cell"` 和 `"per_cell_data"` | 模板绑定器使用 `"per_cell"` 键 |

### 6. analysis_v3/__init__.py — 移除 classify_all_tables 调用

| 改动 | 说明 |
|------|------|
| 删除 `from table_classifier import classify_all_tables` | 不再需要 |
| 删除 `classify_all_tables(doc.tables)` 调用 | 不再需要 |
| 删除 `extract_table_surroundings()` 调用 | 不再需要 |
| 删除 `table_results["_classification"]` 赋值 | 不再需要 |
| 删除空 `try/except` 块 | 清理残留 |

### 7. schemas.py — 移除 scoring 后补逻辑

| 改动 | 说明 |
|------|------|
| 删除 `_convert_table_classification_scoring()` 函数 | 评分维度只从 LLM 提取 |
| `assemble_v3_analysis_data()` 移除 `table_classification` 参数 | 不再传入 |
| 删除 `result["table_classification"] = table_classification` | 不再写入 analysis_data |

### 8. technical.py — 改为从 template_tables 读取

| 改动 | 说明 |
|------|------|
| 新增 `_find_sections_by_type(req_secs, file_type)` | 按 file_type 查找章节 |
| 新增 `_table_to_dicts(headers, rows)` | 表格行列转 dict 列表 |
| `_collect_from_tech_tables` 改为迭代 `sec["template_tables"]` | 不再读 `table_classification.tech_requirements` |
| `_collect_from_product_lists` 改为迭代 `sec["template_tables"]` | 不再读 `table_classification.product_lists` |

### 9. business.py — 改为从 template_tables 读取

| 改动 | 说明 |
|------|------|
| 新增 `_find_sections_by_type` 和 `_table_to_dicts` | 同 technical.py |
| `_collect_from_business_tables` 改为迭代 `sec["template_tables"]` | file_type=business |
| `_collect_from_service_tables` 改为迭代 `sec["template_tables"]` | file_type=service |

### 10. analysis.py — 更新 table_classification 引用

| 改动 | 说明 |
|------|------|
| `tc = v3_data.get("table_classification")` → `v3_data.get("format_requirements", {})` | 仅用于兜底读取 |

### 11. table_classifier.py — 标记废弃

| 改动 | 说明 |
|------|------|
| 文件头部添加 `# DEPRECATED` 注释 | 保留文件供回滚参考 |

### 删除的功能清单

| 删除的功能 | 行数 | 原因 |
|-----------|------|------|
| `classify_all_tables()` | ~120 | 独立的表格分类不再需要 |
| `_match_raw_table()` | ~80 | 关键词猜章节归属 |
| `_fill_table_from_original()` | ~80 | 产品库填充逻辑（原路径废弃） |
| `_detect_table_columns()` | ~15 | 默认表格创建逻辑的一部分 |
| `_extract_table_data_from_analysis()` | ~120 | 默认表格创建逻辑的一部分 |
| `_normalize_row_heights()` | ~30 | 仅被上述函数调用 |
| `_convert_table_classification_scoring()` | ~80 | 评分后补逻辑 |
| `_raw_product_tables` 全局列表 | ~30 | 拍平所有表格的错误做法 |
| `_raw_product_lists` 全局列表 | ~20 | 拍平所有产品的错误做法 |
| `table_classification` 字段 | ~10 | 从 analysis_data 移除 |

## 验证

- 语法检查：11 个修改文件全部通过
- 单元测试：9 项覆盖关键功能
- 现有测试：73 项全部通过
- 集成测试：analysis_data 无 table_classification、消费者正确读取 template_tables
