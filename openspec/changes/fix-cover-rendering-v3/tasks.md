## 1. 删除 LLM 封面填充

- [ ] 1.1 删除 `_llm_fill_cover_blocks` 函数（3728-3809行）
- [ ] 1.2 删除第 3856 行对 `_llm_fill_cover_blocks` 的调用

## 2. 封面数据源改为 section_lookup

- [ ] 2.1 在 pre-TOC 封面渲染处，添加从 `analysis_result.analysis_data.format_requirements.section_lookup` 按标题查找 cover section 的逻辑
- [ ] 2.2 添加降级逻辑：section_lookup 中找不到时回退到 outline item 的 template_content
- [ ] 2.3 添加降级逻辑：template_content 为空时使用默认 fallback（"投标文件"）

## 3. 封面边距应用

- [ ] 3.1 在 pre-TOC 封面渲染前，从 `format_requirements.page_margins` 读取边距并设置到 `document.sections[0]`
- [ ] 3.2 封面渲染完成后（分页到目录前），恢复默认边距

## 4. 封面 title 渲染

- [ ] 4.1 在 pre-TOC 封面渲染中，先渲染 section 的 title 行，使用第一个 template_content block 的字体信息
- [ ] 4.2 确保 title 默认居中、22pt bold、宋体

## 5. "正本"表格宽度和定位

- [ ] 5.1 将 `_cell.width` 从 `Cm(1.5)` 改为 `Cm(3.0)`
- [ ] 5.2 将 `tblpX` 改为 `5580000`（15.5cm 距左边缘，确保 3cm 宽的表格右边距页面右边 2.5cm）
- [ ] 5.3 确认 `tblpY=900000` 保持不变（2.5cm 距上边距）

## 6. 第二个封面分页

- [ ] 6.1 在主循环第二个封面渲染前加 `document.add_page_break()`
- [ ] 6.2 第二个封面渲染后保留 `document.add_page_break()`

## 7. 验证

- [ ] 7.1 确认 `document_parser.py` 的页面边距捕获未提交修改存在且正确
- [ ] 7.2 确认 `analysis_v3/__init__.py` 的边距注入未提交修改存在且正确
- [ ] 7.3 确认 `Path` 导入在 helpers.py 第 8 行存在
- [ ] 7.4 启动服务验证封面渲染正确性
