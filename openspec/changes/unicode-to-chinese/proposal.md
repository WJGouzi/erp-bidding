# Proposal: Unicode Escape Sequences → Raw Chinese Characters

## Motivation

项目代码中存在大量 `\uXXXX` 形式的 Unicode 转义序列来表示中文字符，导致：
- 代码可读性极差，无法直接看出字符串的语义
- 代码审查困难，必须借助工具解码才能理解内容
- 与项目中直接使用中文的其他代码风格不一致

## Scope

将 `app/` 目录中所有非必需的 Unicode 转义替换为原始中文字符。

### 包含（Category A + B）

| 文件 | 改动量 | 内容 |
|---|---|---|
| `helpers.py` | ~60 处 | 注释、logger 消息、LLM prompt、免责声明、字体名、dict key |
| `table_parser.py` | 3 处 | dict 匹配 key（内容/说明/要求） |
| `phase3_scoring.py` | 1 处（可选） | 正则中的中文关键词 |

### 排除（Category C）

- `document_parser.py` 中的框线绘图字符
- `table_parser.py` 中的 `\u3000`（全角空格）
- `helpers.py` 中的标点符号列表（，；：等）
- 所有文件中的 Unicode 范围表达式（`\u4e00-\u9fff` 等）
- 特殊符号（`\ufeff`、`\u2605` 等）

## Risk

- 低风险：所有改动均为字符串字面量替换，不影响运行逻辑
- Python 3 原生支持源码中的中文（UTF-8）
- 改后需确认 `helpers.py` 等文件头部有 `# -*- coding: utf-8 -*-` 或使用 UTF-8 编码

## Verification

- 改后运行 `python3 -c "import app.service_modules.task_pipeline.helpers"` 确认无语法错误
- 改后 `git diff` 确认只有字符编码变化，无其他逻辑变化
