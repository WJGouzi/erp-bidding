## Why

标书生成系统在分析招标文件和生成标书内容时，存在多处信息截断问题：分析LLM只读取招标文件前6000字符、生成Prompt只引用原文前3000字符、招标文件全文Chroma向量在生成阶段闲置、知识库检索结果被过度截断。这些截断导致生成的内容无法严格遵循招标文件全文要求，关键的技术参数、资格条件、评分标准等信息可能在传递过程中丢失。

## What Changes

- **修复分析阶段LLM截断**：`_extract_structured_analysis_with_llm` 不再用 `cleaned_text[:6000]`，改为全文分段多次提取或使用长上下文模型
- **修复生成阶段原文引用截断**：`_generate_chapter_content` 不再用 `effective_text[:3000]`，改为从招标文件Chroma向量库按章节语义检索相关原文片段
- **启用招标文件Chroma向量检索**：在生成阶段增加对 `"bidding"` 集合的 `query_documents` 调用，让招标文件全文参与语义检索
- **修复知识库检索截断**：提高 `top_k`、取消 `snippet[:500]` 截断、增加可用的上下文片段数量
- **修复产品库检索截断**：提高 `top_k`、取消 `[:300]` 截断
- **新增知识库引用校验**：生成阶段引用知识库内容时，增加回溯验证机制，确认引用内容在知识库中真实存在
- **新增标书类型差异化提示**：补全 `_get_bid_type_prompt_profile` 的空实现，为GOODS/SERVICE/ENGINEERING分别定制生成提示
- **新增生成后对标验证**：生成完成后，将生成内容与招标原文进行自动化逐条核对，输出对标报告

## Capabilities

### New Capabilities
- `tender-content-fidelity`: 招标文件内容忠实传递，消除分析/生成各阶段的信息截断
- `kb-citation-verification`: 知识库引用回溯校验，确保引用的知识库内容真实可用
- `post-generation-validation`: 生成后对标验证，将标书内容与招标要求逐条比对
- `bid-type-customization`: 货物/服务/工程三类标书的生成提示差异化

### Modified Capabilities
- （无现有spec需要修改）

## Impact

- **app/service_modules/task_pipeline/analysis.py**: `_extract_structured_analysis_with_llm` 入参逻辑修改
- **app/service_modules/task_pipeline/helpers.py**: `_generate_chapter_content`、`_build_knowledge_base_context`、`_build_product_context`、`_build_generation_coverage_snapshot` 修改
- **app/service_modules/task_pipeline/generate.py**: `_complete_generate` 新增对标流程
- **app/infrastructure/integrations.py**: 可能需要对ChromaAdapter增加批量查询能力
- Chroma服务端（外部）：无改动，依赖现有接口
- 无新增外部依赖
