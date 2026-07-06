## Why

当前标书生成管道的 `_generate_chapter_content` 存在架构性缺陷：有模板的章节最终会落入 LLM 生成路径，导致 LLM 扩写模板内容、编造数据。同时，轮廓中的"响应文件分隔页"在正文中被渲染为一级标题，而非独立的分隔页。这两个问题直接违反 AGENTS.md 的规则（模板优先、数据溯源、章节分页），导致生成的标书质量不可用。

## What Changes

1. **重写 `_generate_chapter_content` 的执行流**：删除"所有路径最终落到 LLM"的设计。改为四阶段判断——有模板→复制填空/留空；分隔页→标记特殊类型；分类引擎（TEXT/TABLE/QUALIFICATION）→确定性处理；仅 FREE_WRITE（无模板、无分类、非分隔页的未知章节）→允许 LLM
2. **新增分隔页渲染逻辑**：`_build_docx_bytes` 中检测"响应文件"类分隔页，不渲染为 heading，改为独立居中分隔页；原分隔页下的子章节提升为 level=1
3. **新增 TEXT_TEMPLATE 的留空阻断**：分类为 TEXT_TEMPLATE 但原文中找不到模板文本时，返回 `_EMPTY_PAGE_MARKER`（留空），不落入 LLM
4. **新增模板存在性二次校验**：`bind_template` 匹配到 `required_sections` 但 `has_template=False` 时，检查该 section 是否确实有 `template_content` 定义——如果有但绑定失败，直接留空，不走 LLM

## Capabilities

### New Capabilities
- `template-content-gate`: 有模板章节的严格门控——当章节在 format_requirements 中有模板定义时，仅走模板路径，不得进入 LLM；模板加载失败则留空
- `separator-page-renderer`: 响应文件分隔页在正文中的特殊渲染——识别"响应文件"类容器页，渲染为独立居中分隔页，其子章节以 level=1 显示
- `text-template-empty-fallback`: TEXT_TEMPLATE 分类章节在原文无法匹配模板文本时的留空策略——不落入 LLM，直接返回空页标记

### Modified Capabilities
- (无需修改现有 specs，本次不涉及需求层面的变化，仅为实现层面的修正)

## Impact

- **核心文件**：`app/service_modules/task_pipeline/helpers.py`（`_generate_chapter_content` 和 `_build_docx_bytes`）
- **辅助文件**：`app/service_modules/task_pipeline/generate.py`（可能需要导出新增常量）
- **无影响**：`template_binder.py` 逻辑不变；数据库 schema 不变；API 接口不变
- **风险**：部分原由 LLM 生成内容的章节会变为留空，需人工补充；这是 AGENTS.md 要求的正确行为
