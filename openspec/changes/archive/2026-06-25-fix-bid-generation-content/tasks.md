## 1. 分析阶段全文传递

- [x] 1.1 修改 `_extract_structured_analysis_with_llm`：移除 `cleaned_text[:6000]` 截断，传入完整 cleaned_text；若超出模型上下文则分块提取 + 合并
- [ ] 1.2 回归验证：`test_generation_context_usage.py` 和 `test_outline_leaf_generation.py` 中涉及 analysis_result 的测试用例保持通过

## 2. 生成阶段招标文件原文检索

- [x] 2.1 新增 `_build_tender_chroma_context(task, chapter_title, chapter_desc)` 函数：查询 `CHROMA_COLLECTION` 集合（`"bidding"`），用章节标题+描述做语义检索，`top_k=8`，返回完整chunk
- [x] 2.2 修改 `_generate_chapter_content`：移除 `effective_text[:3000]` 的注入，改为调用 `_build_tender_chroma_context` 并将结果注入到 Prompt 的"招标需求依据"位置

## 3. 知识库检索优化

- [x] 3.1 修改 `_build_knowledge_base_context`：移除 `search_text = (query_text or "")[:1000]` 截断，使用完整 query_text（上限放宽至5000字符）
- [x] 3.2 提高 `top_k` 从5到15
- [x] 3.3 移除 `doc.strip()[:500]` 截断，保留完整chunk
- [x] 3.4 修改 Prompt 组装：每个知识库最多取前10个片段（从3提高到10）

## 4. 产品库检索优化

- [x] 4.1 修改 `_build_product_context`：提高 `top_k` 从3到10
- [x] 4.2 移除 `doc.strip()[:300]` 截断

## 5. 标书类型差异化提示

- [x] 5.1 补全 `_get_bid_type_prompt_profile`：为 GOODS/SERVICE/ENGINEERING 分别定义写作重点指令文本
- [x] 5.2 修改 `_generate_chapter_content`：在 system_prompt 尾部按 `task.bid_type` 注入差异化指令

## 6. 知识库引用校验

- [x] 6.1 新增 `_verify_kb_citations(generated_content, knowledge_contexts)` 函数：对生成内容中疑似引用知识库的段落进行 Chroma 反向检索验证
- [x] 6.2 修改 `_complete_generate`：每章生成完成后调用引用校验，结果标记 VERIFIED / UNVERIFIED 存入 `generation_coverage_snapshot`

## 7. 生成后对标验证

- [x] 7.1 增强 `_build_generation_coverage_snapshot`：从 `analysis_data` 读取原子要求项列表，逐项检查生成内容中是否有对应响应
- [x] 7.2 修改 `_complete_generate`：在 DOCX 组装后、任务标记完成前，调用对标验证，遗漏要求写入 `task.error_message`
- [x] 7.3 更新 `get_generate_progress`：在对标报告存在时，返回值中包含 `coverage_report` 字段

## 8. 测试验证

- [x] 8.1 更新 `test_outline_leaf_generation.py`：为 tender_chroma_context 新增测试用例
- [x] 8.2 更新 `test_generation_context_usage.py`：验证分析阶段全文传递效果
- [x] 8.3 运行所有现有测试，确认无回归
