# 设计文档：表格章节绑定修复

## 问题分析

### 问题一：表格存储结构冗余

当前 `extract_format_requirements()` 返回结构：

```json
{
  "chapter_title": "...",
  "required_sections": [
    {"title": "一、供应商基本情况表", "template_tables": [table_a]},
    {"title": "二、类似项目业绩一览表", "template_tables": [table_b]}
  ],
  "template_tables": [table_a, table_b],  // ← 冗余：等于 required_sections[*].template_tables 的平铺
  ...
}
```

顶层 `template_tables` 与 `required_sections[].template_tables` 数据完全重复，不仅浪费存储，更在于消费端可能混淆该从哪个路径读取。

### 问题二：table_classification 与章节脱离

`analysis_data.table_classification` 按类型（preliminary, product_lists, scoring, tech_requirements 等）将表格独立分类存放，这些分类与文档的实际章节没有对应关系。导致：
- 无法确定表格属于哪个章节
- 表格与文字段落顺序丢失
- 类型标签基于关键词猜测，容易错分

### 问题三：生成时表格定位到文档末尾

生成的 DOCX 中所有 5 张表格全部出现在文档最后（"八、其他材料"之后），而非对应章节位置。

根因追踪到 `_write_outline_item` 中的 `_last_element` 局部变量：

```python
def _write_outline_item(outline_item, level=1, ...):
    h = document.add_heading(title, level=min(level, 4))
    _last_element = h._element  # 局部变量，仅在当前章节有效
    
    # ContentBlock 渲染路径
    for _block in _chapter_cc:
        if _block["type"] == "paragraph":
            _p = document.add_paragraph(...)
            _last_element = _p._element  # add_paragraph 追加到末尾
        elif _block["type"] == "table":
            _tbl = write_table_from_data(document, td, insert_after=_last_element)
            _last_element = _tbl
```

`document.add_paragraph()` 始终将新段落追加到文档 body 末尾，而 `write_table_from_data(insert_after=...)` 将表格插入到 `_last_element` 之后。当 ContentBlock 路径中段落和表格交替渲染时，`_last_element` 的链应该正确串联。

但问题在于：章节递归调用造成 `_last_element` 在各帧之间不共享。对于子章节中需要写入的表格，`_last_element` 只指向子章节标题元素，而非父章节最后一个内容元素。

### 问题四：合并单元格数据传递失效

`_extract_template_tables` 从 `ContentBlock` 读取 `merge_cells` 时，如果 `per_cell_data` 存在，`merge_cells` 属性从 `per_cell_data.get('merge_cells', [])` 读取。但在 `to_per_cell` → `to_dict` 链中，`_rebuild_merge_cells` 重建的合并信息可能与原始 XML 不一致。

---

## 修复方案

### 修复 1：删除 format_requirements 顶层 template_tables

**文件**: `phase1_5_format.py`

删除：
```python
# phase1_5_format.py 第425-432行
all_tables = []
for rs in required_sections:
    section_title = rs.get("title", "")
    for tbl in rs.get("template_tables", []):
        tbl["title"] = section_title
        all_tables.append(tbl)
```

以及从返回值中删除 `"template_tables": all_tables`。

**风险**：需要确认所有消费方都已迁移到 `required_sections[].template_tables` 路径。经搜索，`helpers.py` 中共 10 处引用 `format_requirements`，其中 `_generate_table_content`（第2405行）已正确使用 `required_sections[].template_tables`。其余引用主要读取 `section_lookup` 和 `chapter_title`，不受此影响。

### 修复 2：确保 analysis_data 不包含 table_classification

**文件**: `analysis_v3/__init__.py`

当前第686行有注释 "# 不再需要独立的 table_classification" 但缺少实际的清理代码。`assemble_v3_analysis_data()` 在 `schemas.py` 中已不生成 `table_classification`，但需确认：
1. 没有其他代码路径（如分段分析 `_run_segmented_analysis`）意外注入 `table_classification`
2. 旧数据中已有的 `table_classification` 在读取时被忽略

**操作**：在 `assemble_v3_analysis_data` 返回结果后，显式 `analysis_data.pop("table_classification", None)` 作为防御。

### 修复 3：_write_outline_item 中 _last_element 追踪修复

**文件**: `helpers.py` 第3820-4020行

**核心思路**：`_last_element` 不能在嵌套递归中丢失。改用一个贯穿所有章节的外层追踪变量。

```
修改前：_last_element 是 _write_outline_item 的局部变量
       每调用一次 _write_outline_item 就重新从 heading 开始
       → 子章节的表格插入到子章节标题后，但后续父章节的其他内容会追加到 body 末尾
        
修改后：使用一个外层作用域的 OrderedDict 或列表追踪所有已写入的 block 元素
       每个 _write_outline_item 调用均参考全局追踪
```

**具体方案 A（推荐）**：在 `_write_outline_item` 外部使用 `nonlocal` 或 list 引用：

```python
_last_elements = []  # 追踪所有 write 操作的顺序

def _write_outline_item(outline_item, level=1, ...):
    h = document.add_heading(title, level=min(level, 4))
    _last_elements.append(h._element)
    
    # 写入段落
    _p = document.add_paragraph(...)
    _last_elements.append(_p._element)
    
    # 写入表格 — insert_after 取上一个元素的引用
    _ref = _last_elements[-1] if _last_elements else None
    _tbl = write_table_from_data(document, td, insert_after=_ref)
    if _tbl is not None:
        _last_elements.append(_tbl)
```

**具体方案 B**：直接传递 `_last_element` 作为参数给子章节
```python
def _write_outline_item(outline_item, level=1, ..., _last_element=None):
```

方案 A 更简单，推荐。

### 修复 4：write_table_from_data insert_after 定位确认

**文件**: `table_codec.py` 第347-351行

当前代码：
```python
if insert_after is not None:
    ref_elem = insert_after
    ref_elem.addnext(tbl)
```

需要确认 `addnext()` 在 python-docx/lxml 中的行为 — 它会在 `ref_elem` 的直接后面插入。如果 `ref_elem` 在 body 中不存在（例如是段落内部的 run 元素），则表格可能被插入到错误位置。

**操作**：增加防御检查，确保 `insert_after` 是 body 的直接子代（`w:p` 或已插入的 `w:tbl`）。如果不是，回溯找到最近的 body 子代。

### 修复 5：merge_cells 正确传递

**文件**: `phase1_5_format.py` 第290-305行（`_extract_template_tables`）

当前 `_extract_template_tables` 从 `block.merge_cells` 读取，而当 `per_cell_data` 存在时该属性返回 `per_cell_data.get('merge_cells', [])`。`to_dict()` 中的 `_rebuild_merge_cells` 可能因为 `col_span` 计算方式与原始 XML 不一致而产生偏差。

**操作**：在 `_extract_template_tables` 中优先从 `per_cell_data` 读取原始 merge_cells，而不是依赖 `_rebuild_merge_cells` 重建。

### 修复 6：清理 table_classifier.py

**文件**: `table_classifier.py`

- 确认 `classify_all_tables` 和 `extract_table_surroundings` 不再被任何代码调用
- 在文件顶部添加明确的 DEPRECATED 标记和移除建议

---

## 数据流（修复后）

```
解析阶段：
  DOCX → Section[]
         → phase1_5_format()
              → required_sections[].template_tables  ← 表格唯一存储
              → 不输出顶层 template_tables
              → 不输出 table_classification

生成阶段：
  _write_outline_item(chapter)
    → 写入章节标题
    → 写入段落文本（document.add_paragraph → 追加到末尾）
    → 更新全局 _last_elements
    → 写入表格（write_table_from_data insert_after=_last_elements[-1]）
    → 更新全局 _last_elements
    → 递归处理子章节（共享全局 _last_elements）
```

## 不修改的范围

- `schemas.py`: `assemble_v3_analysis_data` 已经正确，不需要修改
- `analysis.py`: 第266行的 `v3_data.get("format_requirements", {})` 后备读取逻辑不变
- `technical.py`, `business.py`: 已正确从 `template_tables` 读取
