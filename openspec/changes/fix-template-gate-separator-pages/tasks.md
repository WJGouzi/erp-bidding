## 1. 常量与辅助函数（helpers.py）

- [x] 1.1 在 `helpers.py` 的常量区新增 `_SEPARATOR_PAGE_PREFIX = "[[SEPARATOR_PAGE]]"` 和 `_SEPARATOR_PAGE_EMPTY = "[[SEPARATOR_PAGE_EMPTY]]"`，与 `_EMPTY_PAGE_MARKER` 并列
- [x] 1.2 新增 `_is_separator_page_title(title)` 函数：检测标题是否含资格性/符合性/技术/商务/其他响应文件等关键词
- [x] 1.3 新增 `_render_separator_page(doc, outline_item, effective_text)` 函数：渲染居中分隔页（二号宋体加粗、页面上方留白 6-8 行）

## 2. 重写 _generate_chapter_content 的执行流

- [x] 2.1 在 `template_binder` 调用后增加二次校验：遍历 `_fmt.required_sections`，若找到匹配条目且 `template_content` 非空但 `bind_template` 未返回 `has_template=True`，则返回 `_EMPTY_PAGE_MARKER`（D1a 模板存在性门控）
- [x] 2.2 在 `template_binder` 路径之后、`_classify_chapter_type` 之前，插入分隔页检测：调用 `_is_separator_page_title(title)`，匹配则返回 `_SEPARATOR_PAGE_PREFIX + 原文片段(如有)` 或 `_SEPARATOR_PAGE_EMPTY`（D1b 分隔页检测）
- [x] 2.3 修改 TEXT_TEMPLATE 路径：`_detect_template_type` 返回空时直接 `return _EMPTY_PAGE_MARKER`，不再继续执行（D1c TEXT_TEMPLATE 阻断）
- [x] 2.4 确认分类引擎使用 `if/elif/else` 结构（TEXT→TABLE→QUALIFICATION），每个分支都有 return，不产生跨越
- [x] 2.5 确认 FREE_WRITE（LLM 路径）仅在以上所有门控均未命中时才执行（D1d）

## 3. 修改 _build_docx_bytes 的主循环

- [x] 3.1 在主循环（`for _oi_idx, item in enumerate(outline)`）中，在封面跳过逻辑之后、分页符之前，插入分隔页检测：调用 `_is_separator_page_title(item.get("title", ""))`，匹配则调用 `_render_separator_page` 后 `continue`
- [x] 3.2 分隔页渲染后在主循环中手动添加 `document.add_page_break()`
- [x] 3.3 分隔页判定后，获取其 `children` 列表，逐个以 `level=1` 调用 `_write_outline_item(child, level=1)`（而不是 `level+1`），确保子章节以一级标题渲染

## 4. 处理 _generate_chapter_content 的返回值解析

- [x] 4.1 在 `_build_docx_bytes` 中，处理 `_SEPARATOR_PAGE_PREFIX` 前缀的内容：分隔页内容已由 `_render_separator_page` 处理，不再需要 `chapter_contents` 匹配
- [x] 4.2 确认 `_SEPARATOR_PAGE_EMPTY` 标记的分隔页：仅渲染标题，不插入额外内容

## 5. 验证与测试

- [x] 5.1 检查代码静态语法：`cd /Users/qiaoqiaogouzi/Desktop/javaDemo/bidding/erp-bidding && python -m py_compile app/service_modules/task_pipeline/helpers.py`
- [x] 5.2 验证现有单元测试仍通过：`cd /Users/qiaoqiaogouzi/Desktop/javaDemo/bidding/erp-bidding && python -m pytest tests/ -v 2>&1 | tail -30`
- [x] 5.3 检查修改后的代码是否仍有任何路径使有模板的章节落入 LLM（代码审查 `_generate_chapter_content` 所有 return 语句）
- [x] 5.4 检查修改后的代码是否仍有任何路径将分隔页渲染为 heading（代码审查 `_build_docx_bytes` 主循环）

## 6. 目录层级修复（根据用户反馈补充）

- [x] 6.1 修复 `_assign_numbers`：封面的 `is_cover` 字段不再被 `is_volume_label` 覆盖，两个字段同时保留
- [x] 6.2 修复 `_parse_format_tree`：去掉 `template_tables` 的关键词重叠过滤，改为直接保留原表

## 7. 修复分隔叶子节点内容丢失（严重数据丢失）

- [x] 7.1 发现根因：分隔页的子节点是 outline 嵌套节点，没有独立 chapter_record，
    _write_outline_item 找不到内容（无 _chapter_idx，无 inherited_child_sections）
- [x] 7.2 在 generate.py 主循环后，遍历分隔页的子节点，调用 _generate_chapter_content
    生成内容，追加到 chapter_contents（子节点通过标题匹配找到内容）
- [x] 7.3 新增 import _is_separator_page_title 到 generate.py
- [x] 7.4 语法检查通过，33 个单元测试通过

## 8. 修复 ContentBlocks 路由丢失（模板章节正文消失）

- [x] 8.1 发现根因：`_build_chapter_contents_from_records` 提取 content_blocks 后将 content 设为空字符串，但 `_write_outline_item` 的 `if matched_content:` 对空字符串为 falsy，导致 content_blocks 永远不会被处理
- [x] 8.2 修复 `_write_outline_item`：在 `if matched_content:` 条件中增加 content_blocks 存在性检测，确保 content_blocks 能被正确渲染
- [x] 8.3 语法检查与清理 `__pycache__`

## 9. 修复表格/段落交叉顺序（DOCX 两遍扫描问题）

- [x] 9.1 发现根因：`_parse_docx_structured` 先遍历段落（第1遍）、再遍历 body 追加表格（第2遍），所有段落都在表格前，丢失原文交叉顺序
- [x] 9.2 修复 `ContentBlock`：新增 `merge_cells`、`column_widths` 字段（to_dict / from_dict 同步）
- [x] 9.3 修复 `_parse_table`：提取合并单元格（gridSpan / vMerge XML）和列宽（table.columns）
- [x] 9.4 修复 `_parse_docx_structured` body 循环：用 `target.content.insert(pos, block)` 替代 `append`

## 10. 表格保真度提升（方案A：全链路传递格式）

- [x] 10.1 修复 `phase1_5_format.py:_extract_required_sections`：table 模板内容传递 `merge_cells`、`column_widths`
- [x] 10.2 修复 `template_binder.py`：ContentBlock 增加 `column_widths` 字段，全链路透传
- [x] 10.3 修复 `helpers.py:_write_outline_item`：渲染表格时应用列宽和合并单元格
- [x] 10.4 语法检查与 `__pycache__` 清理
