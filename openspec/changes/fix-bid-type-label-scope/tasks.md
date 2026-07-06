## 1. 修复 bid_type_label_map 作用域

将 `helpers.py` 中 `_generate_chapter_content` 函数的 `bid_type_label_map` 定义从 `except` 块内移到 `try-except` 之前。

- 文件: `app/service_modules/task_pipeline/helpers.py`
- 行 2795（`bid_type_label_map = {...}` 所在行）
- 操作: 剪切该行到 `try:` 语句之前
- 验证: 确认模板绑定成功后不再抛出 `UnboundLocalError`
