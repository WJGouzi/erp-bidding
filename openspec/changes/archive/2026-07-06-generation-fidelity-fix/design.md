# 生成保真修复 — 设计

> 本文档描述生成保真修复的改造方案。
> 核心思想：生成侧不再"自由综合"，而是"严格遵从原始招标文件的格式和内容"。

---

## 设计 1：目录净化

### 现状

`catalog.py` 中的目录构建函数 `_build_package_aware_outline()` 从多个来源合并：

```python
# catalog.py 中的多条规则
("评分|评选|评审", "评分响应"),       # L56：将评分章节错误加入目录
("评审", "评分响应"),                  # 模板推断加入
# check_items 展开为"资格证明文件 8.1~8.24"
# scoring 展开为"评分响应"
```

### 改造

目录的**唯一来源**改为 `format_requirements.required_sections`。

```python
# 改后逻辑
def _build_catalog_outline(analysis_data):
    """目录只从 required_sections 构建"""
    format_req = analysis_data.get("format_requirements", {})
    sections = format_req.get("required_sections", [])
    if not sections:
        # 降级：使用旧逻辑（保留向后兼容）
        return _legacy_build_outline(analysis_data)
    
    outline = []
    for s in sections:
        outline.append({
            "title": s.get("title", ""),
            "description": s.get("description", ""),
            "children": s.get("children", []),
        })
    return outline
```

具体操作：

| 改动 | 文件 | 说明 |
|------|------|------|
| 删除 `("评分\|评选\|评审", "评分响应")` 映射 | `catalog.py` | 防止评分章节误入目录 |
| 删除 `_classify_check_items()` 中 scoring 分类展开 | `catalog.py` | 禁止 check_items 机械展开为目录条目 |
| 删除模板库中"评分响应"等模板条目 | `catalog.py` | 清理模板源中的冗余 |
| 新增 `_validate_catalog_against_format()` | `catalog.py` | 校验目录与格式要求一致 |
| `catalog_inference.py` 中移除 `"评分响应"` 硬编码 | `catalog_inference.py` | 清理推断逻辑 |

---

## 设计 2：缺失归位

### 现状

`helpers.py` L4255-4290 的 `_write_missing_requirements_page()` 将所有缺失项集中写入一个独立板块：

```python
def _write_missing_requirements_page():
    document.add_heading("待人工补齐清单", level=1)
    for item in missing_items:
        p.add_run(f"{target_title} [{status}]")  # ← [PENDING] 暴露
        p.add_run(f"招标文件原文提示：{json_data}")  # ← JSON 泄露
```

### 改造

每章节生成时自行处理缺失：

```
_generate_chapter_content() 返回值扩展为：
  {
    "content": str | list[ContentBlock],    # 章节正文
    "has_gaps": bool,                       # 是否有缺失项
    "gap_details": [                        # 缺失详情（供调试，不进入 docx）
      {"field": "品牌", "requirement": "..."}
    ]
  }

docx 组装阶段：
  for block in content_blocks:
    if block.type == "paragraph": write_paragraph(block)
    if block.type == "table": write_table(block)
  if result.has_gaps:
    # 在本章节末尾追加空行 + 提示
    write_paragraph("（待补充）")
```

删除 `_write_missing_requirements_page()` 整函数及调用。

---

## 设计 3：模板锁定与两阶段生成

### 现状

所有章节走同一 LLM 生成路径，系统无法区分"有模板"和"无模板"。

### 改造

新增 `app/service_modules/task_pipeline/template_binder.py`，实现两阶段：

```
阶段 A — template_binding：

  def bind_template(chapter_title, format_requirements):
      """
      检查 format_requirements 中是否有本章节的模板
      返回：TemplateBinding 或 None
      """
      for section in format_requirements.required_sections:
          if section.title matches chapter_title:
              if section.has_template:
                  return TemplateBinding(
                      chapter_title=chapter_title,
                      template_blocks=section.content_blocks,  # 原文模板
                      placeholders=extract_placeholders(section),
                      has_template=True,
                  )
      return TemplateBinding(
          chapter_title=chapter_title,
          has_template=False,
      )


阶段 B — content_filling：

  def fill_content(binding, subject_context, knowledge_context, product_context):
      if binding.has_template:
          # 复制模板 → 填空
          filled_blocks = []
          for block in binding.template_blocks:
              if block.type == "paragraph":
                  text = block.text
                  for ph in block.placeholders:
                      value = resolve_fill(ph.field_name, ...)
                      text = text.replace(ph.marker, value or ph.marker)
                  filled_blocks.append(ParagraphBlock(text))
              elif block.type == "table":
                  # 表格：原样复制结构，只填空缺单元格
                  table_data = copy.deepcopy(block.table_data)
                  for row in table_data.rows:
                      for cell in row.cells:
                          if cell.is_empty():
                              cell.value = resolve_fill(cell.field_name, ...) or ""
                  filled_blocks.append(TableBlock(table_data))
          return filled_blocks
      
      else:
          # 无模板：LLM 生成
          return llm_generate(chapter_title, analysis_context, knowledge_context)
```

### TemplateBinding 数据结构

```python
@dataclass
class TemplateBinding:
    chapter_title: str
    has_template: bool
    template_blocks: list[ContentBlock] = field(default_factory=list)
    placeholders: list[Placeholder] = field(default_factory=list)

@dataclass
class ContentBlock:
    type: str  # "paragraph" | "table"
    # paragraph:
    text: str = ""
    placeholders: list[Placeholder] = field(default_factory=list)
    # table:
    table_data: dict | None = None  # {"headers": [...], "rows": [[...]]}

@dataclass
class Placeholder:
    marker: str         # 原文占位符，如 "XXX"
    field_name: str     # 标准字段名，如 "company_name"
    fill_type: str      # "subject" | "knowledge" | "product" | "unknown"
    fallback: str = ""  # 找不到时的兜底
```

### 填空源映射

```
field_name           → 数据源
────────────────────────────────────────
company_name         → subject_context.company_name
project_name         → bidder_notice.project_name
project_no           → bidder_notice.project_no
bid_date             → 当前日期
legal_person         → subject_context.legal_person
authorized_person    → 未提供则留空
brand                → product_context.product_data[品名].brand
spec                 → product_context.product_data[品名].spec
...
```

---

## 设计 4：封面修复

### 现状

`_build_docx_bytes()` 中封面是硬编码生成的：

```python
document.add_heading("投标文件", level=0)  # 固定标题
p.add_run(f"标的名称：{cover_item_name}")
p.add_run(f"项目编号：{cover_project_no}")
```

### 改造

```python
def _build_cover_page(document, format_requirements, subject_context, bidder_notice):
    """优先使用招标文件封面模板，无模板则使用自有模板"""
    cover_template = _find_cover_template(format_requirements)
    
    if cover_template:
        # 使用招标文件的封面
        for block in cover_template.content_blocks:
            if block.type == "paragraph":
                text = _fill_placeholders(block, subject_context, bidder_notice)
                document.add_paragraph(text)
            elif block.type == "table":
                _write_filled_table(document, block, subject_context, bidder_notice)
    else:
        # 自有封面模板
        document.add_heading("投标文件", level=0)
        _write_field(document, "标的名称", bidder_notice.get("project_name", ""))
        _write_field(document, "项目编号", bidder_notice.get("project_no", ""))
        _write_field(document, "投标人名称", subject_context.get("company_name", ""))
        _write_field(document, "投标时间", utc_now().strftime("%Y年%m月%d日"))
        _write_field(document, "包号", bidder_notice.get("package_no", ""))
```

关键原则：**找不到的项目信息留空，不编造**。

---

## 设计 5：表格桥梁与混合排版保真

### 现状

解析侧正确识别了表格，但数据在 `analysis_data` 中以文本形式传递，生成侧无法重建表格。

### 改造

#### 5a：数据结构定义

在 `analysis_data` 中增加结构化表格存储：

```python
# analysis_data 新增字段
"format_requirements": {
    "required_sections": [
        {
            "title": "一、法定代表人授权书",
            "description": "...",
            "has_template": True,
            "content_blocks": [
                {"type": "paragraph", "text": "本授权声明：XXX..."},
                {
                    "type": "table",
                    "headers": ["姓名", "职务", "职称", "学历", "专业"],
                    "rows": [
                        ["", "", "", "", ""],
                        ...
                    ],
                    "merge_cells": [
                        {"row_start": 0, "row_end": 0, "col_start": 0, "col_end": 1}
                    ]
                },
                {"type": "paragraph", "text": "附：身份证复印件..."}
            ]
        }
    ]
}
```

#### 5b：传递通道

`_build_chapter_contents_from_records()` 输出的 `content` 支持 ContentBlock 列表：

```python
# chapter_records 中新增字段
chapter.content_blocks = [
    {"type": "paragraph", "text": "..."},
    {"type": "table", "headers": [...], "rows": [[...]]},
]
chapter.content = ""  # 旧字段保留，但生成 docx 时优先使用 content_blocks
```

#### 5c：docx 写入

`_build_docx_bytes()` 中的章节写入逻辑：

```python
def _write_chapter(document, chapter):
    if chapter.content_blocks:
        for block in chapter.content_blocks:
            if block["type"] == "paragraph":
                p = document.add_paragraph(_strip_xml_control_chars(block["text"]))
                _set_paragraph_style(p, "正文")
            elif block["type"] == "table":
                _write_structured_table(document, block)
    else:
        # 降级：使用旧文本内容
        _write_formatted_content(document, chapter.content)
```

### 混合排版顺序保证

`content_blocks` 列表严格按原文顺序构建，写入 docx 时按索引顺序逐块写入，段落和表格交替出现时**顺序不变**。

---

## 设计 6：内容状态检测

### 现状

LLM 在生成时即使原文已有内容，也可能进行改写/美化。

### 改造

填充前的检测算法：

```python
def classify_content_state(text: str) -> ContentState:
    """
    判断原文中一个字段/区域的状态
    """
    stripped = text.strip()
    if not stripped:
        return ContentState.EMPTY       # 完全空白
    if stripped in ("XXX", "___", "...", "—"):
        return ContentState.PLACEHOLDER  # 标准占位符
    if re.match(r'^20\d{2}年\s*月\s*日$', stripped):
        return ContentState.PLACEHOLDER  # 日期占位
    if len(stripped) <= 2 and stripped in ("—", "-"):
        return ContentState.PLACEHOLDER  # 占位线
    return ContentState.FILLED          # 已有内容
```

规则：

| 状态 | 允许的操作 |
|------|-----------|
| EMPTY | 可查询知识库/产品库填充；找不到则留空 |
| PLACEHOLDER | 按 field_name 类型查询对应数据源填充 |
| FILLED | **锁定，不得修改** |

此规则适用于：
- 模板中的每个占位符
- 表格中的每个单元格
- 文字段落中的空缺处
