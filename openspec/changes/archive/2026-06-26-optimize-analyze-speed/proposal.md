## Why

分析接口 POST /api/bidding/tasks/{task_id}/analyze 存在严重的性能问题：两个串联的 ChromaDB 轮询（各最多等 10 分钟）、两次串行的 LLM API 调用、以及长文档分块串行提取。对于一份大型招标文件，分析阶段可能耗费 5~30+ 分钟。

## What Changes

- **消除 ChromaDB 等待**：上传文件时同步解析文本并缓存到本地临时文件，分析阶段直接从缓存读取文本，不再轮询 ChromaDB
- **合并 LLM 调用**：分包检测和结构化分析合并为一次 LLM 调用，Prompt 同时输出包信息和结构化 JSON
- **分块并行提取**：长文档分块后用 ThreadPoolExecutor 并发调用 LLM，将 N 次串行调用降为 ≈1 次时间
- **API 接口无变化**：所有优化均为 service 层内部实现

## Capabilities

### New Capabilities
- `analyze-text-cache`: 上传阶段同步解析文件文本并缓存，消除分析阶段对 ChromaDB 就绪的依赖
- `merged-llm-analysis`: 分包检测和结构化分析合并为一次 LLM 调用

### Modified Capabilities
- （无现有 spec 需要修改）

## Impact

- **app/service_modules/storage.py**: `save_bytes` 新增文本缓存逻辑
- **app/service_modules/task_pipeline/analysis.py**: `_complete_analysis` 删除 Chroma 轮询；`_extract_structured_analysis_with_llm` 合并分包检测；`_extract_structured_analysis_chunked` 并行化
- **app/service_modules/task_pipeline/helpers.py**: `_build_shared_resource_analysis_text` 优先读缓存
- **app/service_modules/task_pipeline/workflow.py**: `create_original_task` 上传后同步解析并缓存
- **API 层 (api/tasks.py)**: 无变化
