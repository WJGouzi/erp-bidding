## ADDED Requirements

### Requirement: 响应文件分隔页在正文中作为独立分隔页渲染

当 `_build_docx_bytes` 遍历轮廓时，系统 SHALL 检测"响应文件"类分隔页条目，不将其渲染为 heading，而是渲染为独立居中分隔页。

#### Scenario: 分隔页检测命中
- **WHEN** 轮廓条目的 title 包含"资格性响应文件"、"符合性响应文件"、"技术响应文件"、"商务响应文件"、"其他响应文件"或"其他文件"
- **THEN** 该条目被判定为分隔页

#### Scenario: 分隔页渲染样式
- **WHEN** 判定为分隔页
- **THEN** 在页面正中以二号宋体加粗渲染标题；页面上方保留 6-8 行空白；不显示章节编号

#### Scenario: 分隔页有招标原文内容
- **WHEN** 分隔页判定命中，且 `effective_text` 中存在该分隔页对应章节的原始文本
- **THEN** 在分隔页标题下方插入原文内容（仿宋小四，带首行缩进）

#### Scenario: 分隔页无招标原文内容
- **WHEN** 分隔页判定命中，但招标原文中无对应内容
- **THEN** 仅渲染标题本身，不输出"（待补充）"或其他占位提示

### Requirement: 分隔页与普通章节互斥

分隔页条目 SHALL 在主循环中由 `_render_separator_page` 单独处理，不进入 `_write_outline_item`。普通章节（非分隔页）保持原有 `_write_outline_item` 处理路径不变。

#### Scenario: 主循环拦截
- **WHEN** `_oi_idx` 对应的条目被判定为分隔页
- **THEN** 该条目不进入 `_write_outline_item`，不被渲染为 heading

#### Scenario: 普通章节不受影响
- **WHEN** 条目不是分隔页
- **THEN** 保持原有 `_write_outline_item` 渲染路径

### Requirement: 分隔页的子章节以 level=1 渲染

分隔页的子章节 SHALL 在正文中以 `level=1`（一级标题）渲染，不继承分隔页的逻辑层级。

#### Scenario: 子章节提升
- **WHEN** 分隔页的 children 列表逐个进入 `_write_outline_item`
- **THEN** 使用 `level=1` 而非 `level+1`，确保子章节以一级标题渲染

#### Scenario: 子章节非分隔页
- **WHEN** 分隔页下的某个子章节本身也匹配分隔页关键词（罕见情况）
- **THEN** 该子章节仍按分隔页处理，不降级
