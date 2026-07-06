## ADDED Requirements

### Requirement: TEXT_TEMPLATE 分类在找不到模板文本时留空

当 `_classify_chapter_type` 将章节分类为 `CHAPTER_TYPE_TEXT_TEMPLATE`（承诺函、声明函、授权书等固定格式文本），但 `_detect_template_type` 无法从招标原文中匹配到模板文本时，系统 SHALL 返回 `_EMPTY_PAGE_MARKER`（留空），绝不通过 LLM 或其他方式填充内容。

#### Scenario: 模板文本找到，正常填空
- **WHEN** `_classify_chapter_type` 返回 `CHAPTER_TYPE_TEXT_TEMPLATE` 且 `_detect_template_type` 返回非空模板文本
- **THEN** 进入填空路径：`_fill_template` 替换占位符后返回填充文本，流程正常结束

#### Scenario: 模板文本未找到，留空
- **WHEN** `_classify_chapter_type` 返回 `CHAPTER_TYPE_TEXT_TEMPLATE` 但 `_detect_template_type` 返回空字符串
- **THEN** 直接返回 `_EMPTY_PAGE_MARKER`，不继续执行后续任何代码

#### Scenario: 原文锁定校验失败，保留填充后原文
- **WHEN** 模板文本找到并填空后，`_verify_template_diff` 发现原文被意外修改
- **THEN** 保留填充后文本，输出 warning 日志，但不降级到 LLM 或其他填充方式

### Requirement: 分类引擎路径终结原则

所有分类引擎（TEXT_TEMPLATE、TABLE_TEMPLATE、QUALIFICATION）SHALL 在各自路径中终结：要么返回有效内容，要么返回 `_EMPTY_PAGE_MARKER`。各引擎之间 SHALL 使用 `if/elif/else`（或等效的提前 return）结构，确保只执行一个引擎路径，不产生跨越。

#### Scenario: 单一引擎路径执行
- **WHEN** `chapter_type == CHAPTER_TYPE_TEXT_TEMPLATE`
- **THEN** 仅执行 TEXT_TEMPLATE 路径，完成后 return，不继续判断 TABLE_TEMPLATE 或 QUALIFICATION

#### Scenario: 引擎返回空
- **WHEN** 分类引擎路径执行后没有有效内容也没有 `_EMPTY_PAGE_MARKER`
- **THEN** 该分类应被视作异常，记录 warning 日志后返回 `_EMPTY_PAGE_MARKER`

### Requirement: FREE_WRITE 为唯一的 LLM 入口

在所有模板门控、分隔页检测、分类引擎均未命中后，系统 SHALL 仅通过 `CHAPTER_TYPE_FREE_WRITE` 路径进入 LLM 生成。LLM 路径 SHALL 保持现有的 system_prompt 和 user_parts 组装逻辑不变。

#### Scenario: FREE_WRITE 进入 LLM
- **WHEN** 章节无模板、非分隔页、且未被任何分类引擎识别
- **THEN** 进入 LLM 生成路径（system_prompt + user_parts + LLMAdapter 调用）

#### Scenario: 所有阻断在前
- **WHEN** 章节有模板或匹配分类引擎
- **THEN** FREE_WRITE 路径不会被执行
