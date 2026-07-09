## Why

另一个对话的修改覆盖了之前封面的正确修复，引入了三个问题：
1. 新增了 `_llm_fill_cover_blocks` 对封面模板进行 LLM 填充，违反了 AGENTS.md 规则
2. 封面数据源从 `format_requirements.section_lookup` 回退到了 outline item（数据不完整）
3. 封面边距、第二个封面分页、"正本"渲染仍有缺陷

## What Changes

1. **删除 LLM 封面填充**：移除 `_llm_fill_cover_blocks` 函数及调用，封面内容只从模板原文渲染
2. **封面数据源修正**：封面模板通过 `format_requirements` 的 `section_lookup` 按标题查找，而不是从 outline item 读取
3. **封面边距应用**：使用分析阶段捕获的文档原始边距（`page_margins`）渲染封面
4. **第二个封面分页**：主循环中第二个封面渲染前插入分页符，渲染后也分页
5. **"正本"表格宽度调整**：列宽从 1.5cm 增加到 3cm，确保 "正本" 横向排列
6. **标题渲染**：封面渲染时先渲染 section 的 title，再渲染 template_content

## Capabilities

### New Capabilities

- `cover-title-margin`: 封面标题渲染、边距应用、"正本"浮动定位
- `cover-data-source`: 封面模板数据源从 `format_requirements.section_lookup` 获取

### Modified Capabilities

None.

## Impact

- `app/service_modules/task_pipeline/helpers.py` — `_build_docx_bytes` 封面渲染逻辑（删除 LLM 填充、修改数据源、调整边距和"正本"）
- `app/infrastructure/document_parser.py` — 页面边距捕获（已有未提交修改）
- `app/service_modules/task_pipeline/analysis_v3/__init__.py` — 边距注入（已有未提交修改）
- `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py` — 封面检测和 section_lookup（需验证已有逻辑）
