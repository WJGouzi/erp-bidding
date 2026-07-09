## Why

三个封面渲染缺陷：封面标题（title）和边距未渲染、第二个封面与前序章节混在同一页、"正本"标记未正确出现在封面页。

## What Changes

1. **封面渲染增加 title + template_texts**：pre-TOC 封面渲染时，先渲染 cover section 的 title（作为封面标题），再渲染 template_texts（原文辅助文本），最后渲染 template_content 块。
2. **边距处理**：封面段落使用与原始招标文件一致的边距信息。分析接口新增 `margin` 字段（上/下/左/右）。当分析数据中无边距信息时，使用默认边距（上下 2.54cm，左右 3.17cm）。
3. **第二封面分页修复**：主循环中遇到第二个封面时，先执行 `document.add_page_break()` 再渲染模板内容，确保从新页开始。
4. **"正本"定位修复**：在封面模板渲染前预留"正本"浮动位置，确保浮动表格出现在封面页的正确位置。

## Capabilities

### New Capabilities

- `cover-title-margin`: 封面标题渲染与边距处理。包括从 `template_content.font` 提取字号/字体渲染 title，以及从分析数据读取边距信息并应用到封面段落。

### Modified Capabilities

None.

## Impact

- `app/service_modules/task_pipeline/helpers.py` — `_build_docx_bytes` 封面渲染逻辑
- `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py` — 分析阶段可能需传递边距信息
- `app/infrastructure/document_parser.py` — 可能需捕获文档边距
