## Context

当前标书生成系统在传递招标文件内容时存在多处截断：

1. **分析阶段**: `_extract_structured_analysis_with_llm` 截断 `cleaned_text[:6000]`，招标文件后半部分的结构化信息无法进入 `analysis_data`
2. **生成阶段原文引用**: `_generate_chapter_content` 只引用 `effective_text[:3000]`，长文档后半部分原文对生成LLM不可见
3. **招标文件Chroma向量闲置**: 招标文件全文已切片入库到 `"bidding"` 集合，但生成阶段的语义检索只覆盖知识库（`kb_*`）和产品库（`product_library`），未查询招标文件自身
4. **知识库/产品库检索截断**: 搜索词 `[:1000]`、结果片段 `[:500]`/`[:300]`、`top_k=5`/`top_k=3`，大量内容不会传递给生成LLM
5. **知识库引用无验证**: 生成LLM可自由声称引用知识库内容，系统不验证真实性
6. **标书类型无差异化**: `_get_bid_type_prompt_profile` 空实现
7. **无生成后对标**: 没有自动验证生成内容是否覆盖了所有招标要求

## Goals / Non-Goals

**Goals:**
- 分析阶段LLM接收招标文件全文而非截断
- 生成阶段通过Chroma语义检索正确获取招标文件全文相关片段
- 招标文件Chroma向量在生成阶段被正确查询
- 知识库和产品库检索传递完整片段，不截断
- 生成内容对知识库的引用可回溯验证
- 三种标书类型有差异化的生成提示
- 生成后对标验证输出覆盖度报告

**Non-Goals:**
- 前端交互改造（左原文右编辑等）——属于前端需求
- 报价表自动生成——需单独设计
- 流式生成输出——已确认不需要
- 招标文件格式要求自动适配（页边距、字体等）——需单独设计
- 历史标书复用——需单独设计

## Decisions

### D1: 分析阶段使用长上下文模型 + 分块提取策略
- **方案**: 保持调用 `_extract_structured_analysis_with_llm` 的入口不变，入参改为完整文本
- **策略**: 
  - 优先使用支持长上下文的模型（`.env` 中已配置 `qwen-long`）
  - 若全文超出模型上下文限制，则分块（每块8000字符，重叠2000）逐块提取后合并
  - 合并策略：后块覆盖前块中相同结构字段的内容，不同内容追加
- **替代方案考虑**: 放弃LLM提取改为纯RAG → 否决，结构化提取对目录生成和核对项生成至关重要

### D2: 生成阶段改用Chroma语义检索替代原文截断
- **方案**: 新增 `_build_tender_chroma_context(task, chapter)` 函数
  - 对每个章节，用 `chapter_title + chapter_description` 作为查询词
  - 查询 `CHROMA_COLLECTION`（即 `"bidding"` 集合，招标文件所在集合）
  - `top_k=8`，取消片段截断，返回完整Chroma chunk内容
  - 将结果注入到生成Prompt中，替代现有的 `effective_text[:3000]`
- **影响**: `_generate_chapter_content` 中移除 `effective_text[:3000]` 行，替换为tender_chroma_context的注入

### D3: 知识库检索优化
- **方案**: 修改 `_build_knowledge_base_context`
  - `search_text` 不再截断 `[:1000]`，使用完整的 `query_text`（可长达5000字符）
  - `top_k` 从5提高到15
  - 每个snippet不再截断 `[:500]`，保持Chroma chunk的完整性
  - 每个知识库最多取前10个片段（从3提高到10）
- **影响**: Prompt长度会增加，需注意token使用量

### D4: 产品库检索优化
- **方案**: 修改 `_build_product_context`
  - `top_k` 从3提高到10
  - 每个matched_text不再截断 `[:300]`
- **影响**: Prompt长度增加

### D5: 知识库引用校验
- **方案**: 新增 `_verify_kb_citations(generated_content, knowledge_contexts)` 函数
  - 在 `_complete_generate` 中对每个章节生成完成后调用
  - 对生成内容中疑似引用知识库的段落（含知识库名称、文件名等），用Chroma反向检索验证
  - 验证通过的标记为 `VERIFIED`，未通过的标记为 `UNVERIFIED`
  - 结果记录到 `generation_coverage_snapshot` 中
- **影响**: 增加少量生成后处理时间，但提供了关键的引用可信度保障

### D6: 标书类型差异化提示
- **方案**: 补全 `_get_bid_type_prompt_profile` 
  - 为GOODS/SERVICE/ENGINEERING分别定义写作重点提示
  - 在生成Prompt的system_prompt中根据 `task.bid_type` 注入差异化指令
- **影响**: 仅修改helpers.py中的该函数和_generate_chapter_content的system_prompt组装

### D7: 生成后对标验证
- **方案**: 复用并增强 `_build_generation_coverage_snapshot`
  - 生成完成后，在 `_complete_generate` 中调用增强版覆盖率分析
  - 从 `analysis_data` 中读取原子要求项列表
  - 对每项要求，检查生成内容中是否有对应的响应段落
  - 输出对标报告，存入 `analysis_data["generation_coverage"]`
  - 将遗漏要求告警写入 `task.error_message`
- **影响**: 对标报告通过已有API即可查询，不需要新增接口

## Risks / Trade-offs

- **[Risk] Prompt长度大幅增加** → LLM的token消耗和成本可能上升。Mitigation: 保持`max_tokens`配置项可控，用户可根据需要调整。
- **[Risk] Chroma查询增多** → 生成阶段每个章节会额外查询招标文件Chroma集合。Mitigation: 并行化查询，不影响生成总耗时。
- **[Risk] 知识库引用校验不够精准** → 反向检索可能误判。Mitigation: 仅标记置信度不自动删除内容，用户可自行判断。
- **[Risk] 对标报告可能误报** → 语义匹配可能漏判或错判。Mitigation: 对标结果仅作为参考提示，不阻止生成流程。

## Migration Plan

1. 按tasks.md的顺序逐步修改各函数
2. 每次修改后运行对应单元测试确认回归
3. 所有修改完成后，在测试环境用真实招标文件验证全文覆盖效果
4. 确认无误后合并到主分支

## Open Questions

- （无）
