## Context

封面渲染经过两轮修复后，又被另一个对话的修改覆盖，引入了错误的逻辑。当前未提交的修改中：

- `document_parser.py` — 页面边距捕获（正确，保留）
- `analysis_v3/__init__.py` — 边距注入到 format_requirements（正确，保留）
- `helpers.py` — 新增了 `_llm_fill_cover_blocks`、改动了数据源、调整了"正本"定位值

需要撤回 helpers.py 中错误的 LLM 部分，修正数据源和排版。

## Goals / Non-Goals

**Goals:**
- 封面模板数据源改为从 `format_requirements.section_lookup` 按标题查找
- 删除 `_llm_fill_cover_blocks` 函数和调用
- 封面渲染时应用文档原始边距（`page_margins`）
- 封面渲染时先渲染 section title（使用模板字体），再渲染 template_content
- 第二个封面在主循环中先分页再渲染
- "正本"表格列宽 3cm，确保文字横向排列
- "正本"距上边距 2.5cm（tblpY=900000）、距右边距 2.5cm（调整 tblpX）

**Non-Goals:**
- 不修改 outline 结构
- 不修改表格渲染引擎
- 不修改章节内容生成逻辑
- 不涉及免责声明或目录渲染

## Decisions

### D1: 封面数据源从 section_lookup 获取

**当前问题**：pre-TOC 封面渲染从 `_cover_outline_items`（outline 中有 `is_cover=True` 的节点）获取 `template_content`。outline 是目录接口返回的精简数据，不包含完全的 template_content。

**做法**：在 pre-TOC 渲染时，从 `analysis_result.analysis_data.format_requirements.section_lookup` 中按封面标题查找对应的 section，获取其完整的 `template_content`。

```python
# 读取 analysis_data
_ad = json.loads(analysis_result.analysis_data) if isinstance(analysis_result.analysis_data, str) else (analysis_result.analysis_data or {})
_fmt = _ad.get("format_requirements", {})
_sec_lookup = _fmt.get("section_lookup", {})

# 从 outline item 中获取封面标题
_cover_title = _cover_item.get("title", "").strip()
# 按标题从 section_lookup 获取完整数据
from .analysis_v3.phase1_5_format import _clean_section_title as _clean_title
_clean_t = _clean_title(_cover_title)
_cover_section = _sec_lookup.get(_clean_t)
if _cover_section:
    _cover_blocks = _cover_section.get("template_content", [])
else:
    _cover_blocks = []
```

**理由**：section_lookup 是在分析阶段由 `phase1_5_format.py` 构建的，包含完整的 template_content（含原始 font 信息、占位符标记等）。这是数据的正确来源。

### D2: 删除 LLM 封面填充

**做法**：
1. 删除整个 `_llm_fill_cover_blocks` 函数
2. 删除第 3856 行的调用
3. LLM 的占位符填充改为直接字符串替换（已有 `_fill_placeholder_text`）

**理由**：AGENTS.md 规定"禁止 LLM 自由发挥"、"找不到对应内容时直接留空"。封面模板应原文复制，仅对可识别的占位符（如 `（项目名称）`、`XXX`）做简单字符串替换。

### D3: 封面边距应用

**做法**：在 pre-TOC 封面渲染前，创建新的 section 或设置当前 section 的边距：

```python
# 从 format_requirements 读取边距
_pm = _fmt.get("page_margins", {})
if _pm:
    section = document.sections[0]
    section.top_margin = _pm.get("top", 2540000)
    section.bottom_margin = _pm.get("bottom", 2540000)
    section.left_margin = _pm.get("left", 3170000)
    section.right_margin = _pm.get("right", 3170000)
```

**注意**：封面渲染完成后，在切换到目录前恢复默认边距（或保持原样——如果正文也需要同样的边距）。

### D4: 封面 title 渲染

在渲染 template_content 之前，先渲染 section 的 title：

```python
_cover_title = _cover_section.get("title", "").strip() if _cover_section else _cover_item.get("title", "").strip()
if _cover_title:
    _first_font = {}
    if _cover_blocks:
        _first_font = _cover_blocks[0].get("font", {}) or {}
    _fn = _first_font.get("font_name", "") or "宋体"
    _fs = _first_font.get("font_size", 22.0)
    _fb = _first_font.get("bold", True)
    _p = document.add_paragraph()
    _p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _r = _p.add_run(_cover_title)
    _r.font.name = _fn
    _r.font.size = Pt(_fs)
    if _fb:
        _r.bold = True
    _r.element.rPr.rFonts.set(qn("w:eastAsia"), _fn)
```

### D5: "正本"表格宽度与位置

**当前值**（未提交修改中）：
- `_cell.width = Cm(1.5)` — 1.5cm 太窄，导致文字竖向排列
- `tblpX = 6105600`
- `tblpY = 900000`

**修改后**：
- `_cell.width = Cm(3.0)` — 放大到 3cm 确保"正本"横向排列
- `tblpX = 6120000 - Cm(1.5).emu + Cm(3.0).emu` = 右边距 2.5cm 位置，3cm 宽表格的左边缘
- `tblpY = 900000` — 保持不变（2.5cm 距上边距）

计算：右边距 2.5cm → 右边缘位置 = 21cm - 2.5cm = 18.5cm。表格宽度 3cm → 左边缘 = 18.5 - 3 = 15.5cm = 5580000 EMU。

即 `tblpX = 5580000`。

### D6: 第二个封面分页

主循环中第二个封面的处理改为：

```python
if item.get("is_cover"):
    if _first_cover_in_content:
        _first_cover_in_content = False
        continue
    # 先分页
    document.add_page_break()
    # 渲染模板内容
    _cover_title_text = item.get("title", "").strip()
    ...
    # 渲染完后分页
    document.add_page_break()
    continue
```

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| section_lookup 中找不到封面 section（标题清洗后缀不一致） | 降级到 `_normalize_outline_title_for_match` 做模糊匹配；仍找不到则使用 fallback |
| 删除 LLM 填充后封面占位符未被正确替换 | `_fill_placeholder_text` 已包含 (项目名称)、(项目编号)、XXX 等常见占位符替换；复杂情况可扩展替换规则 |
| 边距修改影响整篇文档布局 | 仅在封面渲染前设置边距，封面完成后（分页前）恢复默认值 |
| "正本"浮动表格在不同 Word 渲染器中位置偏差 | tblpX/tblpY 使用标准 OOXML 绝对定位，已在 Word 和 OnlyOffice 验证 |
