# Design: Unicode Escape → Raw Chinese Characters

## Approach

使用 Python 解码替换策略：对于每个 `\uXXXX` 转义序列，手动确认上下文后替换为对应的原始中文字符。

## 工具辅助

编写一次性 Python 脚本来验证替换的正确性：

```python
# decode_check.py - 确认 \uXXXX 实际对应什么中文
import re

def decode_unicode_escapes(text):
    return re.sub(r'\\u[0-9a-fA-F]{4}', lambda m: chr(int(m.group(0)[2:], 16)), text)
```

## 改动策略

### helpers.py

**分组替换，按功能区域：**

1. **L774** — dict key 获取名：`"\u91c7\u8d2d\u4ea7\u54c1\u540d\u79f0"` → `"采购产品名称"`
2. **L2826** — 默认 type label：`"\u8d27\u7269\u7c7b"` → `"货物类"`
3. **L2830** — 默认文本：`"\u6682\u65e0\u62db\u6807\u4f9c\u636e\u6587\u672c\u3002"` → `"暂无招标依据文本。"`
4. **L2852~2913** — 注释 + logger 消息：约 15 处
5. **L2917~2937** — LLM prompt 全文：约 20 行
6. **L2931~2937** — user prompt 片段：约 3 处
7. **L3464~3495** — 免责声明 + 字体名 + 提示文字：约 15 处
8. **L3500~3535** — 注释 + 字体名：约 8 处
9. **L3648~3649** — 字体名：2 处

### table_parser.py

- **L169**: `"\u5185\u5bb9"` → `"内容"`
- **L171**: `"\u8bf4\u660e"` → `"说明"`, `"\u8981\u6c42"` → `"要求"`

### phase3_scoring.py

- **L766**: 正则中 `(?:\u53c2\u6570|\u89c4\u683c|\u578b\u53f7|\u914d\u7f6e|\u6280\u672f\u6307\u6807)` → `(?:参数|规格|型号|配置|技术指标)`

## 安全措施

- 每处替换后运行一次语法检查
- 只改字面量字符串中的转义，不改正则范围
- 不改字符合集中的单个特殊字符
