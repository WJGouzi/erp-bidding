## Why

`table-per-cell-storage` 实现了 per-cell 数据模型和编解码器（`TableCell`、`TableData`、`write_table_from_data()`），但**从未集成到生成管线的组装阶段**。

当前真实执行的表格写入路径有两条：

1. **ContentBlock 路径**（`helpers.py:4132-4166`）：用 `python-docx` 的 `.merge()` API，异常被 `try/except` 吞掉，复杂表格合并失效
2. **旧文本路径**（`helpers.py:4303/4312/4317`）：`_write_table_from_lines(lines, merges=[])` — 永远传空 merges

`table_codec.write_table_from_data()` 存在但**零调用**。`per_cell` 数据存入 MySQL 后从未被读取消费。

## What Changes

1. **`template_binder.ContentBlock` 增加 `per_cell` 字段** — 让模板绑定阶段携带 per-cell 数据
2. **`phase1_5_format.py` 子章节循环补上 `per_cell`** — 当前子章节的表格缺失 per_cell
3. **`helpers.py` ContentBlock 路径改调用 `write_table_from_data()`** — 替代脆弱的 `merge()` API
4. **`helpers.py` 旧文本路径也接入 per-cell** — 当 `per_cell` 可用时走新路
5. **保留向后兼容** — 旧格式作为降级，两者皆可用

## Impact

- `app/infrastructure/table_codec.py` — 可能小修 `write_table_from_data()` 以适配 python-docx Document 对象
- `app/service_modules/task_pipeline/template_binder.py` — `ContentBlock` 加 per_cell 字段
- `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py` — 子章节补 per_cell
- `app/service_modules/task_pipeline/helpers.py` — 消费端改用 `write_table_from_data()`
- `tests/` — 新增集成测试
