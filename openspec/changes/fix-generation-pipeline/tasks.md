## 1. DeepSeek API 配置（无OPENAI兜底）

- [x] 1.1 在 `app/config/__init__.py` 新增 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）、`DEEPSEEK_MODEL_NAME`（默认 `deepseek-chat`）；移除 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL_NAME` 默认配置
- [x] 1.2 修改 `helpers.py:_generate_chapter_content` 中 LLMAdapter 实例化，直接使用 `DEEPSEEK_API_KEY` + `DEEPSEEK_BASE_URL` + `DEEPSEEK_MODEL_NAME`
- [x] 1.3 修改 `catalog.py:extract_catalog_from_file` 中 LLMAdapter 实例化，直接使用 DeepSeek 配置
- [x] 1.4 修改 `mandate_classifier.py:_llm_fallback` 中 LLMAdapter 实例化，直接使用 DeepSeek 配置

## 2. 封面章节拦截修复

- [x] 2.1 重构 `_generate_chapter_content` 的封面匹配逻辑：`_is_cover_chapter` 独立于 `_sec_title`
- [x] 2.2 优先取 `template_content`，降级取 `content_blocks`，有内容则复制返回
- [x] 2.3 取消最终检查（3082行）对封面关键词的拦截

## 3. 异常日志增强

- [x] 3.1 在 `execution.py:_finalize_background_failure` 中增加 `traceback.format_exc()` 堆栈日志

## 4. 移除死代码

- [x] 4.1 删除 `helpers.py` 中的 `_hard_generate`、`_soft_generate`、`_free_generate`
- [x] 4.2 从 `pipeline.py` 的导入列表中移除

## 5. 知识库召回置信度调整

- [x] 5.1 将 `MIN_RECALL_CONFIDENCE` 默认值从 `0.3` 调整为 `0.01`

## 6. 验证与测试

- [x] 6.1 执行现有单元测试
- [ ] 6.2 启动服务，对 task=13 重新生成，验证不再出 ERROR
- [x] 6.3 确认 `.env` 中 `DEEPSEEK_MODEL_NAME` 使用正确的模型名（建议确认 `deepseek-v4-flash` 是否为官方有效名，否则改为 `deepseek-chat`）
