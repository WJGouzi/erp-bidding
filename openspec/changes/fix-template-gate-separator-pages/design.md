## Context

标书生成管道的核心函数 `_generate_chapter_content`（`helpers.py:2783`）存在架构性缺陷：其执行流采用"模板优先→模板失败→LLM兜底"的递进策略。当模板绑定（`template_binder.bind_template`）或模板文本检测（`_detect_template_type`）失败时，代码无阻断机制，直接落入 LLM 生成路径。LLM 在"不得编造"的提示约束下仍会输出内容，导致模板被扩写、数据被编造。

同时，`_build_docx_bytes`（`helpers.py:3342`）将轮廓中的每个条目都渲染为 heading（`add_heading(title, level)`），包括"资格性响应文件"等本应是分隔页的容器节点，导致正文中出现不应存在的一级标题。

当前代码影响范围：
- `_generate_chapter_content`：约 370 行（2783-3158），包含 LLM 的 system_prompt、user_parts 组装、调用与后处理
- `_build_docx_bytes`：约 910 行（3342-4255），包含主循环和 `_write_outline_item` 递归写入

## Goals / Non-Goals

**Goals:**
- 有模板的章节绝不进入 LLM 生成路径（模板绑定失败→留空）
- TEXT_TEMPLATE 分类章节在原文找不到模板文本时→留空，不落入 LLM
- 响应文件分隔页在正文中不渲染为 heading，改为独立分隔页
- 分隔页的子章节提升为 level=1
- 保持 FREE_WRITE（无模板、无分类、非分隔页）的 LLM 路径不变
- 保持 `template_binder.py` 的绑定逻辑不变

**Non-Goals:**
- 不修改分析阶段（phase1_5_format、metadata 提取等）
- 不修改数据库 schema 或 API 接口
- 不修改表格引擎或资格引擎的内部逻辑
- 不涉及图片/素材匹配逻辑的重写（单独的已知问题）

## Decisions

### D1: _generate_chapter_content 执行流重构

**当前流：**
```
template_binder → 失败 → 证据检查 → TEXT/TABLE/QUAL 引擎 → 都无 → LLM (兜底)
```

**改为：**
```
┌─ 章节进入
├─ D1a: 模板存在性门控
│   从 format_requirements.required_sections 中查找当前章节
│   找到且有 template_content → bind_template
│     → 成功→填空→返回 ContentBlocks
│     → 失败→返回 _EMPTY_PAGE_MARKER (留空)
│   找到但 template_content 为空 → 非模板章节，继续往下
│   没找到 → 继续往下
│
├─ D1b: 分隔页检测
│   标题含"响应文件"且为容器节点（有 children）
│   → 返回 _SEPARATOR_PAGE_PREFIX + 原文内容(如有)
│   → 无原文 → 返回 _SEPARATOR_PAGE_EMPTY
│
├─ D1c: 分类引擎
│   TEXT_TEMPLATE → _detect_template_type
│     → 找到模板文本 → _fill_template → 返回
│     → 没找到模板文本 → 返回 _EMPTY_PAGE_MARKER ✨新增阻断
│   TABLE_TEMPLATE → _generate_table_content → 返回
│   QUALIFICATION → _generate_qualification_content → 返回
│   无分类→继续往下
│
└─ D1d: FREE_WRITE (LLM 允许)
   无模板、非分隔页、无分类 → LLM 生成
```

**实现要点：**
- 在 `template_binder` 调用后新增二次校验：如果 `_fmt` 中存在匹配的 `required_sections` 条目且该条目确实有 `template_content`，但 `bind_template` 返回 `has_template=False` → 判定为"有模板但绑定失败"，直接返回 `_EMPTY_PAGE_MARKER`
- TEXT_TEMPLATE 路径在 `_detect_template_type` 返回空后直接 return `_EMPTY_PAGE_MARKER`，不再继续向下执行
- 新增 `_SEPARATOR_PAGE_PREFIX` 和 `_SEPARATOR_PAGE_EMPTY` 两个常量标记

### D2: 分隔页检测与渲染

**检测逻辑（新增辅助函数 `_is_separator_page_title`）：**
```python
def _is_separator_page_title(title):
    """判断标题是否为响应文件分隔页。"""
    if not title:
        return False
    # 关键词匹配：响应文件类标题（容器类型，非具体内容章节）
    keywords = ("资格性响应文件", "符合性响应文件", "技术响应文件",
                "商务响应文件", "其他响应文件", "其他文件")
    return any(kw in title for kw in keywords)
```

**渲染逻辑（在 `_build_docx_bytes` 主循环中）：**
```python
for _oi_idx, item in enumerate(outline):
    if _is_separator_page(item.get("title", "")):
        _render_separator_page(document, item)
        document.add_page_break()
        continue
    # ... 原有封面跳过逻辑 ...
    if _oi_idx > 0:
        document.add_page_break()
    _write_outline_item(item, level=1)
```

**分隔页渲染样式：**
- 居中，大字号（二号，22pt），宋体加粗
- 页面上方留空白（约 6-8 行空行）
- 不显示章节编号
- 如果有招标原文中对应章节的内容（`effective_text` 匹配到的片段）→ 插入
- 如果没有招标原文 → 仅显示标题本身

### D3: 子章节提升为 level=1

当分隔页被识别后，其 children 在渲染时需要提升级别：
```python
def _write_outline_item(outline_item, level=1, ...):
    # 分隔页不在 _write_outline_item 中处理（已在主循环拦截）
    # 分隔页的 children 以 level=1 进入
    for child in children:
        _write_outline_item(child, level=1, ...)  # 改为 level=1 而不是 level+1
```

**实现方式：** 在主循环拦截分隔页后，对其 children 列表逐个调用 `_write_outline_item(child, level=1)`，而不是让分隔页进入递归。

### D4: 常量与标记

在 `helpers.py` 中新增：
```python
_SEPARATOR_PAGE_PREFIX = "[[SEPARATOR_PAGE]]"
_SEPARATOR_PAGE_EMPTY = "[[SEPARATOR_PAGE_EMPTY]]"
```

与已有常量并列：
```python
_EMPTY_PAGE_MARKER = "[[EMPTY_PAGE]]"
_CONTENT_BLOCKS_PREFIX = "[[CONTENT_BLOCKS]]"
```

### D5: 不变的部分

- `template_binder.py` 的 `bind_template` 和 `fill_content` 逻辑不变
- `_generate_table_content` 和 `_generate_qualification_content` 不变
- `_build_docx_bytes` 的表格渲染逻辑（`_write_table_from_lines`）不变
- 目录页（TOC）的生成逻辑不变
- 封面页渲染逻辑不变
- 素材写入逻辑（`_write_subject_materials_for_outline_item`）不变

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 部分原来由 LLM 输出内容的章节（如无模板的 TEXT_TEMPLATE）会变为留空 | 这是 AGENTS.md 要求的正确行为。留空后用户需自行补充，但不会因 LLM 编造导致废标 |
| 分隔页检测的关键词匹配可能误判某些真正的章节标题 | `_is_separator_page_title` 的白名单严格控制，并在首次部署后评估是否需要调整 |
| `bind_template` 的二次校验可能过严（某些章节虽在 required_sections 中但不应视为有模板） | 仅当 `section` 确实有 `template_content`（非空列表）时才触发留空，没有 template_content 的容器类章节不受影响 |
| 需在 rename/refactor 时同步更新 | 本次修改集中在 2 个函数中，不涉及全局 rename |
