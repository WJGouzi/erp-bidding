## Context

标书生成链路有两条独立的 API 依赖：

| 用途 | API | 配置 | .env 状态 |
|------|-----|------|-----------|
| Embedding/切片检索 | 通义千问 | `QWEN_API_KEY` + `QWEN_BASE_URL` | ✅ 已配置 |
| 标书正文生成 | DeepSeek | `DEEPSEEK_API_KEY` + `DEEPSEEK_BASE_URL` | ✅ 已配置 |

生成阶段核心函数 `_generate_chapter_content` 原硬编码使用 `OPENAI_API_KEY`（中国区不可用且未配置），导致所有需 LLM 的章节全部留白。现已新增 DeepSeek 配置。

同时存在以下缺陷：
1. 封面章节拦截逻辑条件不完整，`is_cover_chapter` 依赖 `_sec_title` 导致穿透
2. 异常定位信息不足
3. `_hard_generate`/`_soft_generate`/`_free_generate` 三个死代码
4. `MIN_RECALL_CONFIDENCE=0.3` 过高，知识库片段全量过滤

## Goals / Non-Goals

**Goals:**
- 生成链路 LLMAdapter 直接使用 `DEEPSEEK_API_KEY`，无 OPENAI 兜底
- Embedding/切片链路保持 `QWEN_API_KEY` 不变
- 配置中移除 `OPENAI_*` 相关默认值
- 封面章节从 `format_requirements` 复制模板
- 异常时记录完整堆栈
- 删除死代码

**Non-Goals:**
- 不修改知识库召回算法
- 不重写表格匹配逻辑
- 不修改 `_build_docx_bytes` 的 DOCX 组装逻辑

## Decisions

### D1: DeepSeek API 配置

```python
# config/__init__.py 新增
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_NAME = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-chat")

# 移除或废弃
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
# OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "qwen-long")
```

`LLMAdapter` 实例化改为直接使用 DeepSeek 配置，不做 OPENAI 降级：

```python
adapter = LLMAdapter(
    api_key=current_app.config.get("DEEPSEEK_API_KEY"),
    base_url=current_app.config.get("DEEPSEEK_BASE_URL"),
    default_model=current_app.config.get("DEEPSEEK_MODEL_NAME"),
)
```

### D2: 各 LLMAdapter 调用点修改

| 文件 | 行号 | 当前 | 改为 | 用途阶段 |
|------|------|------|------|---------|
| `helpers.py:_generate_chapter_content` | 3355 | `OPENAI_API_KEY` | `DEEPSEEK_API_KEY` | 生成 |
| `catalog.py:extract_catalog_from_file` | 1316 | `OPENAI_API_KEY` | `DEEPSEEK_API_KEY` | 目录 |
| `mandate_classifier.py:_llm_fallback` | 185 | `OPENAI_API_KEY` | `DEEPSEEK_API_KEY` | 分类 |
| `llm_extractor.py:_get_llm` | 29 | `QWEN_API_KEY`→`OPENAI` | 保持不变 ✅ | 分析阶段 |

### D3: 封面章节拦截修复

章节标题含封面关键词（"响应性文件""响应文件""封面""封皮"）时，在 `required_sections` 中搜索匹配章节：
1. 优先取 `template_content`
2. 降级取 `content_blocks`
3. 有内容则复制返回
4. 都没有则 `_EMPTY_PAGE_MARKER`

取消最终检查（3082行）对封面关键词的拦截，防止"资格性响应文件"被空页拦截。

### D4: 异常日志增强

```python
# execution.py:_finalize_background_failure
import traceback
logger.error("[task] 后台执行失败 type=%s task=%s exec=%s err=%s\n%s",
             execution_type, task_id, execution_id, exc, traceback.format_exc())
```

### D5: 移除死代码

删除 `_free_generate`（helpers.py:2851）、`_hard_generate`（helpers.py:2768）、`_soft_generate`（helpers.py:2802），从 `pipeline.py` 导出移除。

### D6: 置信度阈值调整

`MIN_RECALL_CONFIDENCE` 默认从 `0.3` 改为 `0.01`。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| DeepSeek API 不可用时无 OPENAI 兜底 | 代码中有 `is_available()` 检查 + `_EMPTY_PAGE_MARKER` 降级路径，不影响任务完成 |
| 封面匹配条件放宽后误匹配 | 关键词白名单精确控制，且必须有 `format_requirements` 中的模板内容 |
| 置信度降低后低质量上下文进入 prompt | 置信度标签（EXACT/HIGH/MEDIUM/LOW）已在输出中标注，LLM 可据此判断 |
