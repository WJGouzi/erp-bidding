# Tasks: Unicode Escape → Raw Chinese Characters

## Implementation

- [x] **1. helpers.py L774 — dict key 替换**
  `"\u91c7\u8d2d\u4ea7\u54c1\u540d\u79f0"` → `"采购产品名称"`
  `"\u4ea7\u54c1\u540d\u79f0"` → `"产品名称"`
  `"\u6807\u7684\u540d\u79f0"` → `"标的名称"`

- [x] **2. helpers.py L2826-L2830 — 默认值替换**
  `"\u8d27\u7269\u7c7b"` → `"货物类"`
  `"\u6682\u65e0\u62db\u6807\u4f9d\u636e\u6587\u672c\u3002"` → `"暂无招标依据文本。"`

- [x] **3. helpers.py L2852~2913 — 注释和 logger 消息**
  约 15 处，包括 `D1a`、`D1b`、`D1c`、`D1d` 标题注释和 `logger.info/warning` 消息

- [x] **4. helpers.py L2917~2937 — LLM prompt**
  约 20 行的 system prompt + user prompt 片段

- [x] **5. helpers.py L2931~2937 — user prompt 片段**
  章节标题/说明/标书类型等变量拼接字符串

- [x] **6. helpers.py L3464~3495 — 免责声明 + 字体名**
  6 段免责声明 + 黑体/宋体字体名 + 末尾提示文字

- [x] **7. helpers.py L3500~3535 — 注释和字体名**
  页面设置/默认字体/标题样式注释 + 宋体/仿宋字体名

- [x] **8. helpers.py L3648~3649 — 字体名**
  `\u4eff\u5b8b` → `仿宋`

- [x] **9. table_parser.py L169+L171 — dict key 替换**
  `"\u5185\u5bb9"` → `"内容"`
  `"\u8bf4\u660e"` → `"说明"`
  `"\u8981\u6c42"` → `"要求"`

- [x] **10. phase3_scoring.py L766 — 正则关键词（可选）**
  `\u53c2\u6570` → `参数` 等

## Verification

- [x] **11. 验证 — 语法检查**
  `python3 -c "import app.service_modules.task_pipeline.helpers"` `app.infrastructure.table_parser` `analysis_v3.phase3_scoring`

- [x] **12. 验证 — 逻辑一致性**
  `git diff` 确认只有编码变化
