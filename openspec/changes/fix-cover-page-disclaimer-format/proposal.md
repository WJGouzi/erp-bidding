## Why

当前标书生成在封面渲染、免责声明分页、"正本"标记显示和投标日期填充方面存在四个问题，导致生成的标书封面格式与招标文件原文出入较大，需要修复以符合招标文件格式规范。

## What Changes

1. **封面字体/格式修复**: 封面模板内容的字体、字号未能正确还原招标文件原文的格式，需要确保 `_build_docx_bytes` 中封面块渲染时严格使用从原文提取的 font 元数据（font_name、font_size、alignment），而不是降级到默认值
2. **免责声明页后去除空白页**: 免责声明已成功在一页内渲染，其后的 `document.add_page_break()` 不再需要，应移除或条件化以避免插入额外空白页
3. **"正本"标记渲染修复**: 封面上的"正本"两个字需使用宋体、三号(16pt)、黑色渲染。当前存在字体颜色未设置、定位不准等问题
4. **封面占位符填充增强**: `_fill_placeholder_text` 需要支持"投标日期"占位符替换，投标日期须使用招标文件中的开标时间(bid_open_time)而非当前时间。同时确保项目名称、项目编号等占位符正确填充

## Capabilities

### New Capabilities
- `cover-page-formatting`: 封面格式精确还原 — 从原文提取的 font 元数据（字体名称、字号、对齐方式）在生成封面时忠实应用，不降级到默认值
- `placeholder-filling`: 封面占位符填充 — 识别并填充封面模板中的"（项目名称）"、"（项目编号）"、"投标日期"等占位符，投标日期使用招标文件开标时间

### Modified Capabilities
<!-- No existing spec-level behavior changes needed -->

## Impact

- `app/service_modules/task_pipeline/helpers.py`: `_build_docx_bytes` 函数中的封面渲染逻辑、"正本"渲染、免责声明分页、占位符填充
- `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py`: 封面 font 元数据注入逻辑（如有必要）
- 生成的 docx 标书文件中封面页和免责声明页的渲染效果
