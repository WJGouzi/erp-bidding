## Why

标书生成链路存在配置错误和代码质量问题：生成阶段（标书正文生成）应使用 DeepSeek API，但代码硬编码为 `OPENAI_API_KEY`（中国区无法使用且未配置），导致 LLM 调用不可用，所有需 LLM 的章节全部返回空页。同时存在覆盖章节误判、表格内容断裂、死代码积累、异常信息不足等问题。

## What Changes

1. **生成链路 API Key 改为 DeepSeek（无OPENAI兜底）**：`_generate_chapter_content`、`catalog.py`、`mandate_classifier.py` 中的 `LLMAdapter` 直接使用 `DEEPSEEK_API_KEY`，不保留 `OPENAI_*` 降级
2. **Embedding/切片保留通义千问**：`QWEN_API_KEY` 已正确配置且使用无误，不做改动
3. **配置移除 OPENAI 相关项**：`config/__init__.py` 中删除或废弃 `OPENAI_API_KEY/BASE_URL/MODEL_NAME`
4. **封面章节使用模板复制**：修复 `_generate_chapter_content` 的封面拦截逻辑
5. **移除死代码**：删除 `_hard_generate`、`_soft_generate`、`_free_generate`
6. **增加异常定位日志**：添加完整堆栈输出
7. **知识库召回置信度阈值调整**：`MIN_RECALL_CONFIDENCE` 从 0.3 调低

## Capabilities

### New Capabilities
- `deepseek-api-config`: DeepSeek API 配置与使用，生成链路直接使用 `DEEPSEEK_API_KEY`

### Modified Capabilities
<!-- 无 spec 级别行为变更 -->

## Impact

| 影响范围 | 文件 | 改动 |
|---------|------|------|
| 配置 | `config/__init__.py` | 新增 DEEPSEEK_API_KEY/BASE_URL/MODEL_NAME；移除 OPENAI 默认配置 |
| 生成核心 | `helpers.py` | API Key 改为 DeepSeek + 封面拦截修复 + 死代码删除 |
| 目录提取 | `catalog.py` | API Key 改为 DeepSeek |
| 格式分类 | `mandate_classifier.py` | API Key 改为 DeepSeek |
| 执行层 | `execution.py` | 增加 traceback 日志 |
| 分析引擎 | `llm_extractor.py` | 已有正确的 QWEN 配置，无需改动 |
