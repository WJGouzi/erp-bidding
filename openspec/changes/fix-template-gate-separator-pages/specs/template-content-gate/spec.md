## ADDED Requirements

### Requirement: 有模板章节不得进入 LLM 生成路径

当 `format_requirements.required_sections` 中存在与当前章节标题匹配的条目，且该条目包含非空的 `template_content` 时，系统 SHALL 仅通过模板绑定路径处理该章节，绝不进入 LLM 生成路径。

#### Scenario: 模板绑定成功，填空返回
- **WHEN** `bind_template` 返回 `has_template=True` 且 `fill_content` 成功填充所有内容块
- **THEN** 返回 `_CONTENT_BLOCKS_PREFIX + serialized`，不走任何后续路径

#### Scenario: 模板内容存在但绑定异常
- **WHEN** `format_requirements` 中存在匹配的 section，`section.template_content` 非空，但 `bind_template` 返回 `has_template=False`（如标题去前缀后无法精确匹配）
- **THEN** 返回 `_EMPTY_PAGE_MARKER`（留空），不进入 LLM

#### Scenario: 模板内容存在但填充失败
- **WHEN** `bind_template` 返回 `has_template=True` 但 `fill_content` 返回空列表
- **THEN** 返回 `_EMPTY_PAGE_MARKER`（留空），不进入 LLM

#### Scenario: 章节不在 required_sections 中
- **WHEN** 章节标题在 `format_requirements.required_sections` 中无匹配
- **THEN** 继续后续判断（分类引擎/LLM），不受此门控影响

#### Scenario: required_sections 中无 template_content
- **WHEN** 匹配到 section 但 `section.template_content` 为空或不存在（容器类型章节）
- **THEN** 不触发此门控，继续后续判断

### Requirement: required_sections 匹配的二次校验

`_generate_chapter_content` SHALL 在 `template_binder` 调用后执行二次校验：检查 `_fmt.required_sections` 中是否存在与当前章节标题匹配的条目，以及该条目是否有 `template_content`。如果二者皆满足但绑定未返回 `has_template=True`，则视为"有模板但绑定失败"，返回 `_EMPTY_PAGE_MARKER`。

#### Scenario: 二次校验触发留空
- **WHEN** `bind_template` 因标题格式细微差异（如含括号、换行符）未匹配到 section，但二次校验发现该 section 存在且非空
- **THEN** 判定为有模板但绑定失败，返回 `_EMPTY_PAGE_MARKER`

#### Scenario: 二次校验不触发
- **WHEN** `_fmt` 为空、`required_sections` 不存在、或无匹配章节
- **THEN** 跳过二次校验，继续执行
