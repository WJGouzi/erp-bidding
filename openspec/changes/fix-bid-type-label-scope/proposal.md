## Why

`_generate_chapter_content` 中 `bid_type_label_map` 变量定义在了 `try-except` 的 `except` 块内部，当模板绑定成功（无异常）时变量不会被定义，导致 `UnboundLocalError`。这是模板绑定引擎正常工作后暴露的隐藏 bug。

## What Changes

1. 将 `bid_type_label_map` 的定义从 `except` 块内移至 `try-except` 之前

## Capabilities

### New Capabilities
- 无

### Modified Capabilities
- 无

## Impact

| 影响范围 | 文件 | 改动 |
|---------|------|------|
| 生成核心 | `helpers.py` | 将 `bid_type_label_map` 移出 `except` 块 |
