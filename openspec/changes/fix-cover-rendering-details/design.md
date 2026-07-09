## Context

封面渲染经过上一轮修复后，`template_content` 数据源已切换正确（从 `_section_by_title` 查找），但仍存在三个问题：

1. **标题未渲染**：封面 section 的 `title`（如"资 格 性 响 应 文 件"）未被渲染到封面上。招标文件封面通常包含标题行（居中、大字号、加粗），当前代码只渲染了 `template_content` 中的字段行。
2. **第二个封面分页**：第二个封面在主循环中渲染时，前序章节结尾可能没有分页符，导致第二个封面与前序章节混在同一页。
3. **"正本"定位**："正本"浮动表格渲染在封面模板内容之后，但由于 python-docx 的浮动表格依赖 body 中的 anchor 段落，如果 anchor 段落不在封面页，"正本"会出现在错误页面。

## Goals / Non-Goals

**Goals:**
- 封面渲染时包含 section 的 title，使用 template 字体信息
- 第二个封面渲染前插入分页符
- "正本"正确出现在封面页，边距符合要求
- 分析阶段捕获文档页面边距
- 封面页使用正确的边距

**Non-Goals:**
- 不修改 outline 结构（仍从 `_section_by_title` 查找）
- 不涉及 LLM 生成逻辑
- 不修改表格渲染引擎

## Decisions

### D1: 封面 title 渲染方式

**做法**：在 pre-TOC 封面渲染中，先渲染 title 行（取自 `_cover_section.get("title", "")`），使用第一个 `template_content` block 的 font 信息（或默认宋体 16pt bold），居中。

```python
# 在 _cover_blocks for 循环之前
_cover_title_text = _cover_section.get("title", "").strip()
if _cover_title_text:
    _title_font = {}
    if _cover_blocks:
        _title_font = _cover_blocks[0].get("font", {}) or {}
    _p = document.add_paragraph()
    _p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _r = _p.add_run(_cover_title_text)
    _font_name = _title_font.get("font_name", "") or "宋体"
    _font_size = _title_font.get("font_size", 16.0)
    _font_bold = _title_font.get("bold", False) or True  # title 默认加粗
    ...apply font...
```

**理由**：title 是封面内容的组成部分，招标文件中对封面标题有字体要求。使用第一个 block 的 font 信息是最合理的近似。

### D2: 页面边距处理

**做法**：
1. **分析阶段**：`phase1_5_format.py` 提取文档属性时，读取文档的 section 页面设置，存入 `format_requirements.page_margins`。
2. **渲染阶段**：pre-TOC 封面渲染前，设置文档页面边距。

**边距数据结构**：
```python
page_margins = {
    "top": 2540000,      # EMU (2.54cm)
    "bottom": 2540000,
    "left": 3170000,     # EMU (3.17cm)
    "right": 3170000,
}
```

**渲染方式**：使用 python-docx 的 `section.page_setup` 设置边距。

**默认值**：当分析数据无边距时，使用标准 Word 默认值（上下 2.54cm，左右 3.17cm）。

### D3: 第二个封面分页

**做法**：主循环中处理第二个封面时，在渲染内容前加 `document.add_page_break()`。

```python
if item.get("is_cover"):
    if not _cover_first_skipped:
        _cover_first_skipped = True
        continue
    # 先分页，确保从新页开始
    document.add_page_break()
    # 然后渲染模板内容
    ...
    # 渲染完后分页
    document.add_page_break()
    continue
```

### D4: "正本"定位修复

**问题**："正本"浮动表格在 python-docx 中依赖 body 中的 anchor 段落。如果 anchor 段落位于文档末尾，浮动表格可能定位到错误的页面。

**解决方案**：（简化版）将"正本"表格放在封面模板渲染段落之前，通过绝对定位浮动到封面的右上角。

实际上更可靠的做法是：在封面模板渲染之前创建"正本"表格，使其 anchor 段落位于封面内容之前（保证 anchor 在封面页），然后利用绝对定位将其浮动到封面页右上角。

```python
# 在封面模板渲染之前，创建"正本"浮动表格
_zhengben_table = document.add_table(rows=1, cols=1)
# ...设置样式、内容、绝对定位...
# 然后渲染封面模板内容
for _blk in _cover_blocks:
    ...
```

**理由**：`vertAnchor="page"` 的浮动表格位置由 `tblpY` 指定，不依赖 anchor 段落位置。但某些 Word 实现中，如果 anchor 段落不在视口中，浮动会异常。将 anchor 放到封面内容最前面确保稳定渲染。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 封面 title 字体与原文不一致 | 使用第一个 `template_content` block 的 font，招标文件封面内容块通常有统一字体 |
| 边距改动影响整篇文档 | 封面渲染后立即恢复，不残留到目录/正文 |
| "正本"浮动位置在不同 Word 版本中不一致 | 使用标准的 OOXML `tblpPr` 绝对定位，已在 Word/OnlyOffice 验证 |
| 分析数据无边距时使用默认值可能与原文不符 | 默认使用常见的 2.54cm/3.17cm 标准边距 |
