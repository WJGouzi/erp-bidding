## 1. 分析阶段：捕获页面边距

- [x] 1.1 `document_parser.py` 中在解析 DOCX 时捕获文档 section 的页面边距，存入 `doc.page_margins`；在 `analysis_v3/__init__.py` 中将 `doc.page_margins` 注入 `format_requirements.page_margins`
- [x] 1.2 边距默认值处理：当文档无边距信息时使用标准默认值（上下 2.54cm，左右 3.17cm）

## 2. 渲染阶段：封面 title + 边距 + "正本"定位

- [x] 2.1 pre-TOC 封面渲染中增加 title 行渲染：在 `template_content` 循环前，渲染 section 的 `title`，使用第一个 block 的 font 信息（默认加粗）
- [x] 2.2 封面页边距设置：在封面渲染前读取 `page_margins` 并设置到文档 section
- [x] 2.3 封面渲染后恢复边距：在 `document.add_page_break()` 前调用 `_reset_cover_margins()` 恢复默认边距
- [x] 2.4 "正本"表格 anchor 前移：在封面模板内容渲染之前创建"正本"浮动表格（`tblpX=6120000`, `tblpY=900000`）

## 3. 渲染阶段：第二个封面分页

- [x] 3.1 主循环中第二个封面渲染前插入 `document.add_page_break()`，确保从新页开始
