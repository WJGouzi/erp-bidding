## ADDED Requirements

### Requirement: DeepSeek API 配置
生成链路中的所有 LLMAdapter 调用 MUST 直接使用 `DEEPSEEK_API_KEY`，不做 OPENAI 降级。

#### Scenario: 生成阶段使用 DEEPSEEK_API_KEY
- **WHEN** 调用 `_generate_chapter_content` 生成章节正文
- **THEN** LLMAdapter MUST 使用 `current_app.config.get("DEEPSEEK_API_KEY")` 作为 api_key

#### Scenario: 目录提取使用 DEEPSEEK_API_KEY
- **WHEN** 调用 `catalog.py:extract_catalog_from_file` 提取目录
- **THEN** LLMAdapter MUST 使用 `current_app.config.get("DEEPSEEK_API_KEY")` 作为 api_key

#### Scenario: DEEPSEEK 配置项
- **WHEN** 系统启动时加载配置
- **THEN** `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）、`DEEPSEEK_MODEL_NAME`（默认 `deepseek-chat`）MUST 可从环境变量读取

#### Scenario: Embedding 链路保持不变
- **WHEN** 调用 `multi_recall_engine`、`chroma_files`、`quality_assurance` 中的 EmbeddingClient
- **THEN** MUST 继续使用 `QWEN_API_KEY`，不做改动

### Requirement: 配置中移除 OPENAI 默认值
`config/__init__.py` 中 MUST 移除 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL_NAME` 的默认配置。

#### Scenario: 无 OPENAI 配置项
- **WHEN** 系统启动时加载配置
- **THEN** 配置中不存在 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL_NAME` 的默认值定义

### Requirement: LLM 不可用时异常可定位
当 LLM 调用失败时，系统 MUST 记录完整的异常堆栈信息。

#### Scenario: 异常堆栈记录
- **WHEN** `_finalize_background_failure` 被调用时
- **THEN** 日志 MUST 包含 `traceback.format_exc()` 的完整堆栈输出

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
已不再使用的函数 MUST 被删除。

#### Scenario: 删除未调用函数
- **WHEN** 检查 `_free_generate`、`_hard_generate`、`_soft_generate`
- **THEN** 确认全库无调用点
- **THEN** 删除函数定义
- **THEN** 从 `pipeline.py` 导出列表中移除

### Requirement: 知识库召回置信度阈值可调
`MIN_RECALL_CONFIDENCE` 阈值 SHOULD 设为 `0.01`，避免所有片段被过滤。

#### Scenario: 召回片段不被全量过滤
- **WHEN** `_build_knowledge_base_context` 对召回结果进行置信度过滤
- **THEN** 阈值 `MIN_RECALL_CONFIDENCE` 设为 `0.01`
- **THEN** score ≥ 0.01 的片段被保留
