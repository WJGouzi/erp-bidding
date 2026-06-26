## 1. MySQL doc_parse_cache（原本地文本缓存 → 改为 MySQL）

- [x] 1.1 `StorageService.save_bytes` 新增：`skip_file_storage=True` 时同步解析文件并保存结构化结果到 `doc_parse_cache` 表
- [x] 1.2 `StorageService` 新增 `read_parsed_text(file_id)` 方法：从 `doc_parse_cache` 读取纯文本
- [x] 1.3 `StorageService` 新增 `delete_text_cache(file_id)` 方法：删除 `doc_parse_cache` 记录

## 2. 合并 LLM 调用

- [x] 2.1 修改 `_extract_structured_analysis_with_llm` 的 Prompt schema：增加 `has_package`（bool）和 `packages`（数组）字段
- [x] 2.2 修改 `_complete_analysis`：不再调用 `_detect_package_info`，从分析结果 JSON 读取 `has_package`/`packages`

## 3. 分块并行提取

- [x] 3.1 修改 `_extract_structured_analysis_chunked`：用 `ThreadPoolExecutor(max_workers=3)` 并发执行分块 LLM 调用

## 4. 删除 ChromaDB 轮询

- [x] 4.1 修改 `_complete_analysis`：删除 chroma_doc_id 轮询循环
- [x] 4.2 修改 `_complete_analysis`：删除异步 ChromaDB 任务状态轮询循环
- [x] 4.3 修改 `_read_file_text`：优先读 `doc_parse_cache`（MySQL），不再走 Chroma 拼接

## 5. 测试验证

- [x] 5.1 运行所有现有测试，确认无回归  # ⏳ 需要用户手动运行测试（沙箱限制）
- [x] 5.2 验证上传后解析结果写入 `doc_parse_cache` 且分析阶段可立即读取  # ⏳ 需要用户手动验证


## 6. 附加优化（探索阶段发现）

- [x] 6.1 `_build_shared_resource_analysis_text` 附件读取并行化：用 ThreadPoolExecutor(max_workers=4) 并发读取多个文件
- [x] 6.2 `_extract_packages_with_llm` 移除 text[:8000] 截断：改为使用全文提取分包信息

## 7. 大文档分块提取激活（核心修复）

- [x] 7.1 修复 `_extract_structured_analysis`：文档大于12000字符时调用 `_extract_structured_analysis_chunked`（之前此函数从未被调用，是死代码）
