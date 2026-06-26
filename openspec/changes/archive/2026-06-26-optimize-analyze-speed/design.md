## Context

分析接口性能瓶颈：
1. **ChromaDB 轮询**：两个串联的轮询循环各 120×5s，最多等 20 分钟，只为了拿到文件文本
2. **LLM 调用串联**：分包检测和结构化分析是两个独立 LLM 调用
3. **分块串行**：长文档分块后 N 个 chunk 顺序调用 LLM

核心矛盾：文件上传时 `skip_file_storage=True`，文件内容仅存在 Chroma，分析阶段必须等 Chroma 就绪才能读文本。

## Goals / Non-Goals

**Goals:**
- 消除分析阶段对 ChromaDB 就绪的依赖
- 分包检测和结构化分析合并为一次 LLM 调用
- 长文档分块后并行提取
- API 接口不改变

**Non-Goals:**
- 不改变生成阶段的 ChromaDB 使用（生成阶段仍需 Chroma 向量检索）
- 不改变前端 API 调用方式
- 不改变核对、目录等后续流程

## Decisions

### D1: 本地文本缓存
- **方案**: `StorageService.save_bytes` 中 `skip_file_storage=True` 时，同步解析文件并保存纯文本到 `storage/text_cache/{file_id}.txt`
- **替代方案**: 上传时同步等 ChromaDB → 否决，上传响应变慢
- **替代方案**: 分析阶段异步等 ChromaDB → 否决，当前就是这个，慢

### D2: 单次 LLM 调用合并分包+分析
- **方案**: 修改 `_extract_structured_analysis_with_llm` 的 Prompt 和 schema，增加 `has_package`、`packages` 字段
- `_complete_analysis` 不再调用 `_detect_package_info`（独立的 LLM 调用）
- 直接从分析结果 JSON 的 `has_package`/`packages` 字段读取分包信息

### D3: 分块并行
- **方案**: `_extract_structured_analysis_chunked` 中 `concurrent.futures.ThreadPoolExecutor(max_workers=3)`
- **限制**: 最大 3 个并发，避免打爆 API 限流

### D4: 删除 ChromaDB 轮询
- `_complete_analysis` 中删除 [A] chroma_doc_id 轮询和 [B] 异步任务状态轮询
- 文本直接从本地缓存读取（`_read_file_text` 回退链已支持 LOCAL 存储）

## Risks / Trade-offs

- **[Risk] 本地缓存磁盘占用**：招标文件文本通常不大（<50MB），每个任务一个缓存文件，风险低
- **[Risk] 缓存一致性**：如果 ChromaDB 入库失败，文本缓存仍存在 → 分析可正常完成 → 低风险
- **[Risk] LLM 合并后 Prompt 变长**：多输出包信息字段，token 消耗略有增加 → 低
- **[Risk] 并发 LLM 调用触发限流**：max_workers=3，且在可控范围内

## Migration Plan

1. 按 tasks.md 顺序修改各文件
2. 每步修改后运行测试确认无回归
3. 测试环境验证：上传+分析全流程
