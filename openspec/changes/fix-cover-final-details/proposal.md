## Why

封面渲染仍有多个细节问题：第二封面缺少"正本"文字、正本定位有误、page_margins 数据未实际使用造成冗余、封面内容字段填充不完整。这些问题导致生成的标书封面格式不正确。

## What Changes

- 正本 `tblpY` 从 1400000 改为 406400（距页面顶部约两行高度）
- 第二封面渲染时增加"正本"浮动表格（与第一封面一致）
- 删除 `document_parser.py` 中 page_margins 捕获代码
- 删除 `analysis_v3/__init__.py` 中 page_margins 注入代码
- 确保两封面的 LLM 内容填充逻辑一致

## Capabilities

### New Capabilities

无（均为现有功能的修复和调整）

### Modified Capabilities

无（没有 spec 级别的需求变更，均为实现细节调整）

## Impact

- `app/infrastructure/document_parser.py`：移除 margin 捕获 ~15 行
- `app/service_modules/task_pipeline/analysis_v3/__init__.py`：移除 margin 注入 ~5 行
- `app/service_modules/task_pipeline/helpers.py`：修改两处 tblpY 值 + 第二封面增加正本渲染代码
