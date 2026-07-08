## Tasks

### Phase 1: 修改消费者（不改运行时）

- [x] 1.1 `check_items/technical.py` — 添加 `_find_sections_by_type(required_sections, file_type)` 辅助函数
- [x] 1.2 `check_items/technical.py` — 修改 `_collect_from_tech_tables` 改为从 `required_sections` 读取
- [x] 1.3 `check_items/technical.py` — 修改 `_collect_from_product_lists` 改为从 `required_sections` 读取
- [x] 1.4 `check_items/business.py` — 修改 `_collect_from_business_requirements` 和 `_collect_from_service_requirements` 改为从 `required_sections` 读取
- [x] 1.5 `schemas.py` — 移除 `_convert_table_classification_scoring` 调用，评分维度只从 LLM 提取
- [x] 1.6 `helpers.py` `_extract_analysis_context` — 移除 `tc.raw_tables`→`_raw_product_tables` 和 `tc.product_lists`→`_raw_product_lists`
- [x] 1.7 `helpers.py` `_generate_table_content` — 移除 `_match_raw_table()` 调用，改为直接使用当前章节的 template_content

### Phase 2: 删除调用点和降级逻辑

- [x] 2.1 `analysis_v3/__init__.py` — 删除 `classify_all_tables(doc.tables)` 调用
- [x] 2.2 `helpers.py` — 删除 `_match_raw_table()` 函数
- [x] 2.3 `helpers.py` — 删除 `_generate_table_content` 中的默认表格创建逻辑（无匹配时创建默认行）
- [x] 2.4 `schemas.py` — 删除 `_convert_table_classification_scoring` 函数
- [x] 2.5 从 `analysis_data` schema 中移除 `table_classification` 字段

### Phase 3: 清理

- [x] 3.1 `table_classifier.py` — 添加 `# DEPRECATED` 注释
- [x] 3.2 全文搜索 `table_classification` 确保无遗漏引用
- [x] 3.3 验证：用含复杂表格的招标文件跑全流程，检查 `analysis_data` 无 `table_classification`
