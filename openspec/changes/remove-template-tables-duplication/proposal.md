## Why

`template_content` 已按文档解析顺序（文字+表格穿插）完整存储了每个章节的内容，但 `template_tables` 又从同一数据源独立提取了表格副本。两者数据重复、格式可能不一致，且渲染时需额外对齐逻辑才能正确还原文字和表格的混合排版顺序。

## What Changes

- **删除 `template_tables`**：`phase1_5_format.py` 中 `_extract_template_tables()` 及所有调用，required_sections 中 `template_tables` 字段
- **清理关联函数**：删除不再使用的 `_collapse_merged_columns()`、`_build_per_cell()`（若仅被 `template_tables` 使用）
- **消费方迁移**：`catalog.py`、`helpers.py` 中所有读取 `template_tables`（别名 `n_tables`）的地方改为从 `template_content` 中过滤 `type == "table"` 的块获取表格数据
- **表格边框渲染**：DOCX 输出时，表格边框统一设置为黑色实线

## Capabilities

### New Capabilities
无新能力引入，本次变更为纯重构。

### Modified Capabilities
无 spec 级需求变更，改动限实现细节。

## Impact

- `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py` — 删除表格重复提取逻辑
- `app/service_modules/task_pipeline/catalog.py` — 迁移 `n_tables` 引用
- `app/service_modules/task_pipeline/helpers.py` — 迁移 `n_tables` 引用
- `app/service_modules/task_pipeline/template_binder.py` — 确认消费路径正确
- DOCX 渲染层（`helpers.py` 中 `_build_docx_bytes` 或类似函数）— 表格边框样式
- `tests/test_template_binder.py` — 更新测试
