# 封面渲染修复方案

## 背景

当前 `_build_docx_bytes`（`helpers.py`）的封面渲染逻辑存在三个架构性缺陷：

### 缺陷 1：数据源错位

```python
# helpers.py 当前做法
_cover_blocks = _cover_item.get("template_content", [])   # ← 从 outline 拿
_cover_item = _cover_outline_items[0]                     # ← outline 节点
```

**事实**：目录接口的 outline 只返回骨架字段（title, is_cover, is_volume_label, children），**不包含 `template_content`**。`template_content` 只存在于解析数据的 `format_requirements.required_sections` 中。

### 缺陷 2：LLM 降级生成

当 `template_content` 为空时，代码降级生成 LLM 封面。违反 AGENTS.md "找不到对应内容直接留空" 规则。

### 缺陷 3：第 2 封面使用分隔页样式

第 2/后续封面在主循环中调用 `_render_separator_page`，渲染成"6行空行+大号居中标题"的分隔页样式，而不是封面的模板样式。

---

## 数据流

```
解析接口返回:
  analysis_data.format_requirements.required_sections
    ├── section[0]: {title: "资 格 性 响 应 文 件", is_cover: true, template_content: [{text, font}, ...]}
    ├── section[1]: {title: "一、法定代表人授权书", ...}
    ├── ...
    └── section[9]: {title: "其 他 响 应 文 件", is_cover: true, template_content: [{text, font}, ...]}

目录接口返回:
  outline[0]: {title: "资 格 性 响 应 文 件", is_cover: true, is_volume_label: true}
  outline[1]: {title: "一、法定代表人授权书", ...}
  ...

helpers.py 渲染时:
  拿到 outline 节点 → 取其 title → 从 required_sections 中按 title 匹配 →
  取匹配到的 section.template_content → 渲染
```

---

## 修改方案

### 修改 1：构建 title → section 查找映射

在 `_build_docx_bytes` 中，利用已有 `_format_requirements` 构建映射表。

**位置**：`helpers.py`，在 pre-TOC 封面渲染之前
**方式**：从 `analysis_context._format_requirements` 读取 `required_sections`，以清洗后的 title 为 key 构建 dict

```python
# 在 _build_docx_bytes 中，已有代码段：
_cover_fmt = analysis_context.get("_format_requirements", {}) if isinstance(analysis_context, dict) else {}

# 新增：按 title 构建格式章节查找映射
_section_by_title = {}
for _sec in _cover_fmt.get("required_sections", []):
    _t = _sec.get("title", "").strip()
    if _t:
        _section_by_title[_t] = _sec
```

---

### 修改 2：pre-TOC 封面渲染切换数据源

**位置**：`helpers.py` 第 3721-3820 行（pre-TOC 封面渲染 + LLM 降级）

**当前逻辑**：
```python
_cover_outline_items = [item for item in outline if item.get("is_cover")]
_cover_template_found = False

for _cover_idx, _cover_item in enumerate(_cover_outline_items):
    if _cover_idx > 0:
        continue  # 第二个跳过
    _cover_blocks = _cover_item.get("template_content", [])  # ← 从 outline 拿，永远为空
    if _cover_blocks:
        ...渲染...
        _cover_template_found = True
    else:
        _cover_template_found = False

if not _cover_template_found:
    ...LLM生成封面...  # ← 违反规则
```

**改为**：
```python
_cover_outline_items = [item for item in outline if item.get("is_cover")]
_cover_rendered = False

for _cover_idx, _cover_item in enumerate(_cover_outline_items):
    # 只提前渲染第一个封面
    if _cover_idx > 0:
        continue
    
    # 从 format_requirements 按标题查找 template_content
    _cover_title = _cover_item.get("title", "").strip()
    _cover_section = _section_by_title.get(_cover_title, {})
    _cover_blocks = _cover_section.get("template_content", [])
    
    if _cover_blocks:
        for _blk in _cover_blocks:
            ...渲染段落/表格，应用 font 信息...
        _cover_rendered = True

# 移除 LLM 降级代码块
# AGENTS.md：找不到对应内容直接留空
```

**渲染段落时的 font 应用**（复用现有逻辑）：
```python
if _blk.get("type") in ("paragraph", "text"):
    _text = _blk.get("text", "") or ""
    _font = _blk.get("font", {}) or {}
    _p = document.add_paragraph()
    
    # 对齐方式
    _alignment = _font.get("alignment", "")
    if _alignment == "center":
        _p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif _alignment == "right":
        _p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif _alignment == "left":
        _p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    else:
        _p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # 占位符填充
    _placeholder = _blk.get("placeholder", False)
    if _placeholder:
        _text = _fill_placeholder_text(_text)  # 保留现有填充函数
    
    _r = _p.add_run(_text)
    _font_name = _font.get("font_name", "") or "宋体"
    _font_size = _font.get("font_size", 16.0)
    _font_bold = _font.get("bold", False)
    ...应用 font 到 run...
```

---

### 修改 3：主循环中第 2 封面渲染切换数据源

**位置**：`helpers.py` 第 4240-4275 行（主循环封面处理）

**当前逻辑**：
```python
if item.get("is_cover"):
    if _first_cover_in_content:
        _first_cover_in_content = False
        continue
    _render_separator_page(document, item, original_text=None)  # ← 分隔页样式
    for _blk in item.get("template_content", []):               # ← 从 outline 拿
        ...渲染...
    document.add_page_break()
    continue
```

**改为**：
```python
if item.get("is_cover"):
    if _first_cover_in_content:
        _first_cover_in_content = False
        continue  # 第一个已提前渲染
    
    # 后续封面：从 format_requirements 查找完整模板内容
    _cover_title2 = item.get("title", "").strip()
    _cover_section2 = _section_by_title.get(_cover_title2, {})
    _cover_blocks2 = _cover_section2.get("template_content", [])
    
    if _cover_blocks2:
        for _blk in _cover_blocks2:
            ...同修改 2 的渲染逻辑，复用同一套模板渲染代码...
    
    document.add_page_break()
    continue
```

**关键变化**：
- 移除 `_render_separator_page(document, item, original_text=None)` 调用
- 不从 `item.template_content` 读取，改为从 `_section_by_title` 查找

---

### 修改 4：删除 LLM 封面降级代码

**位置**：`helpers.py` 第 3782-3817 行

删除以下代码块：
```python
if not _cover_template_found:
    # 自有封面模板
    for _ in range(5):
        document.add_paragraph("")
    title_para = document.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title_para.add_run("投标文件")
    ...全部删除...
```

**理由**：
- AGENTS.md 明确规定"找不到对应内容直接留空，不得自行编造"
- 如果用户需要自有封面，可以通过 API/前端提供默认封面模板

---

### 修改 5：状态变量简化

当前有两个状态变量：
- `_cover_template_found` ← 追踪是否找到模板内容
- `_first_cover_in_content` ← 追踪主循环中是否已跳过第一个封面

合并为一个：
```python
_cover_first_skipped = False  # 主循环中是否已跳过第一个封面
```

---

## 占位符填充增强

当前 `_fill_placeholder_text` 的占位符替换规则需要增强以覆盖招标文件常见格式：

| 占位符模式 | 填充来源 |
|-----------|---------|
| `XXX（单位名称）` | `company_name` |
| `XXX` | `company_name` |
| `（项目名称）` | `cover_item_name` |
| `采购项目名称:` | 不填充（标签原样保留） |
| `采购文件编号:` | 不填充（标签原样保留） |
| `（项目编号）` | `cover_project_no` |
| `[________]` | 留空 |
| `20    年     月     日` | `cover_bid_time`（年份/月份/日期） |

**新增规则**：纯标签类文本（如"采购项目名称:"、"投标单位（盖章）："等）不做任何替换，原样保留。只对带占位符标记的块（`placeholder: true`）执行替换。

---

## 渲染顺序验证

修复后的渲染顺序：

```
Page 1:  免责声明
Page 2:  第一个封面（如有模板）
          正本 ← 浮动在封面上
Page 3:  目录
Page 4+: 主循环
          ├─ is_cover=True? 
          │   ├─ 第一个封面 → skip（已提前渲染）
          │   └─ 后续封面 → 完整模板渲染 → 分页
          ├─ 分隔页 → separator page → 子章节 → 分页
          └─ 普通章节 → heading + content → 分页
```

---

## 修改范围

| 文件 | 修改内容 |
|------|---------|
| `app/service_modules/task_pipeline/helpers.py` | (1) 构建 `_section_by_title` 映射；(2) pre-TOC 封面切换数据源；(3) 主循环第 2 封面切换数据源；(4) 删除 LLM 降级代码块；(5) 简化状态变量 |

**不改动的文件**：
- `phase1_5_format.py` — 封面识别逻辑已正确
- `catalog.py` — outline 字段设计合理，无需添加 `template_content`
- `document_parser.py` — font 元数据已正确捕获
- API 层 — 无需修改

---

## 边界情况

| 场景 | 行为 |
|------|------|
| 0 个封面模板 | 不生成封面，直接从免责声明 → 目录 → 正文，不报错 |
| 1 个封面模板 | 提前渲染，主循环跳过 |
| 2+ 个封面模板 | 第一个提前渲染；后续在主循环中渲染完整模板内容 |
| 封面模板标题在 `required_sections` 中找不到 | 跳过，不渲染（留空） |
| 封面模板的 `template_content` 为空 | 跳过，不渲染 |
| `_format_requirements` 缺失 | 跳过封面渲染，不报错 |
