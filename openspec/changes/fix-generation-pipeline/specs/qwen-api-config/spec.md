## ADDED Requirements

### Requirement: 通义千问 API 配置统一
生成链路中的所有 LLMAdapter 调用 MUST 使用 QWEN_API_KEY（通义千问）而非 OPENAI_API_KEY。当 QWEN_API_KEY 可用时 MUST 优先使用，OPENAI_API_KEY 仅作为兜底。

#### Scenario: 生成阶段使用 QWEN_API_KEY
- **WHEN** 调用 `_generate_chapter_content` 生成章节正文
- **THEN** LLMAdapter MUST 使用 `current_app.config.get("QWEN_API_KEY")` 作为 api_key

#### Scenario: QWEN 配置兜底
- **WHEN** QWEN_API_KEY 未配置时（空字符串）
- **THEN** LLMAdapter MUST 降级使用 `current_app.config.get("OPENAI_API_KEY")`

#### Scenario: QWEN_MODEL_NAME 配置
- **WHEN** 系统启动时加载配置
- **THEN** `QWEN_MODEL_NAME` MUST 可从环境变量读取，默认值 SHOULD 为 `"qwen-plus"`

### Requirement: LLM 不可用时异常可定位
当 LLM 调用失败时，系统 MUST 记录完整的异常堆栈信息，而不仅是异常消息字符串。

#### Scenario: 异常堆栈记录
- **WHEN** `_finalize_background_failure` 被调用时
- **THEN** 日志 MUST 包含 `traceback.format_exc()` 的完整堆栈输出

#### Scenario: 正常降级不记录为 ERROR
- **WHEN** `is_available()` 返回 False（API Key 未配置）
- **THEN** 日志级别 MUST 为 WARNING，不触发 ERROR
- **THEN** 返回 `_EMPTY_PAGE_MARKER`，不抛出异常

### Requirement: 封面章节使用模板复制
封面类章节（标题包含"响应性文件""响应文件""封面""封皮"）在有 `format_requirements` 模板时 MUST 直接复制模板内容，不走 LLM。

#### Scenario: 有模板时复制内容
- **WHEN** 当前章节标题匹配封面关键词
- **WHEN** `format_requirements.required_sections` 中有对应模板内容
- **THEN** 直接返回模板内容块，不调用 LLM

#### Scenario: 无模板时空页
- **WHEN** 当前章节标题匹配封面关键词
- **WHEN** `format_requirements.required_sections` 中无对应模板内容
- **THEN** 返回 `_EMPTY_PAGE_MARKER`（空页），不调用 LLM

### Requirement: 死代码清理
已不再使用的函数 MUST 被删除以避免维护混淆。

#### Scenario: 删除未调用函数
- **WHEN** 检查 `_free_generate`、`_hard_generate`、`_soft_generate` 三个函数
- **THEN** 确认全库无任何调用点
- **THEN** 删除函数定义
- **THEN** 从 `pipeline.py` 导出列表中移除

### Requirement: 知识库召回置信度阈值可调
知识库召回结果的置信度过滤阈值 SHOULD 设为 `0.01`，避免所有片段被过滤导致知识库上下文为空。

#### Scenario: 召回片段不被全量过滤
- **WHEN** `_build_knowledge_base_context` 对召回结果进行置信度过滤
- **THEN** 阈值 `MIN_RECALL_CONFIDENCE` 设为 `0.01`
- **THEN** score ≥ 0.01 的片段被保留
