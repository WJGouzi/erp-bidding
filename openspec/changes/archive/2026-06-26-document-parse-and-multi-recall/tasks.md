## 1. 数据库 Schema 变更

- [x] 1.1 `subject_company` 表新增 `short_name` 字段
- [x] 1.2 `file_storage` 表新增 `file_sha256` 字段
- [x] 1.3 新建 `doc_parse_cache` 表（file_id, file_sha256, parse_version, parsed_json, chunk_count）
- [x] 1.4 新建 `doc_chunks` 表（file_id, chunk_index, content, section_path, content_type, metadata, chroma_id, FULLTEXT ngram 索引）

## 2. 文档解析器 (layout-aware-parser)

- [x] 2.1 `DocumentParser._parse_docx_structured()`: python-docx 读取段落样式(H1~H9)、表格完整结构、列表
- [x] 2.2 `DocumentParser._parse_pdf_structured()`: fitz 逐页判断类型，纯文本页直接提取，扫描页走 PaddleOCR
- [x] 2.3 `PaddleOCRClient`: 封装 PaddleOCR API（异步提交任务+轮询结果），支持多页并发
- [x] 2.4 `DocumentParser._reconstruct_layout()`: PDF 坐标聚类重建版面（标题区/正文区/表格区）
- [x] 2.5 `DocumentParser.parse_structured()`: 统一入口，返回结构化文档模型 JSON
- [x] 2.6 `DocumentParser.semantic_chunk()`: 按标题/表格自然边界切片，每个 chunk 携带 section_path

## 3. Embedding 客户端

- [x] 3.1 `EmbeddingClient`: 封装通义千问 text-embedding-v4 API
- [x] 3.2 支持批量 embedding（一次最多 10 个文本）
- [x] 3.3 自动重试 + 错误处理

## 4. ChromaDB 直连适配层 (direct-chromadb)

- [x] 4.1 `ChromaDBClient`: 基于 httpx 的 ChromaDB REST API 封装
- [x] 4.2 实现: heartbeat, create/get/delete collection, upsert, get, query, delete
- [x] 4.3 自动 provision（tenant/database 不存在时创建）
- [x] 4.4 重试机制（网络抖动兼容）
- [x] 4.5 统一标识符规范化（`_norm_ctx`）

## 5. 文件入库流程重写 (chroma_files.py)

- [x] 5.1 `ingest_file_to_chroma()` 重写：本地解析→切片→千问 embedding→双写入库（ChromaDB + MySQL doc_chunks）
- [x] 5.2 招标文件入库 → `collection=tender`，知识库文件入库 → `collection={公司简称}`
- [x] 5.3 `delete_file_chroma_documents()` 重写：从 ChromaDB 和 MySQL doc_chunks 同时删除
- [x] 5.4 解析结果缓存写 `doc_parse_cache`
- [x] 5.5 入库前检查缓存（file_sha256 + parse_version）

## 6. 多路召回引擎 (multi-recall)

- [x] 6.1 `MultiRecallEngine.recall()`: 统一入口，同时走向量+关键词两路
- [x] 6.2 `_vector_search()`: ChromaDB query + metadata 过滤
- [x] 6.3 `_keyword_search()`: MySQL FULLTEXT MATCH…AGAINST IN BOOLEAN MODE
- [x] 6.4 `_rrf_fusion()`: Reciprocal Rank Fusion 合并排序
- [x] 6.5 `keyword_extraction()`: LLM 从 query 中提取关键词
- [x] 6.6 结果格式化：每条携带完整出处（file_name, section_path, page, content_type）

## 7. 生成质量保证 (quality-assurance)

- [x] 7.1 `_build_requirement_traceability_matrix()`: 分析阶段构建需求追踪矩阵
  - 从 analysis_data 提取 atomic_requirement_items
  - 和主体已有资质/业绩交叉比对 → 标记 evidence_status
- [x] 7.2 `_bind_requirements_to_chapters()`: 目录阶段用 embedding 语义匹配替代关键字匹配
- [x] 7.3 `_inject_constraints_into_prompt()`: 每章 Prompt 注入精确需求列表+证据状态+废标约束
- [x] 7.4 `_post_generation_verify()`: 生成后逐条校验覆盖率和编造
  - 覆盖率检查：每条相关要求在正文中是否被提及
  - 编造检测：无证据要求是否被 LLM 编造了内容
- [x] 7.5 `_build_coverage_report()`: 生成可读的覆盖率报告，供用户审核

## 8. Prompt 结构调整 (helpers.py)

- [x] 8.1 `_build_tender_chroma_context()` → 改用 `MultiRecallEngine.recall()` 多路召回
- [x] 8.2 `_build_knowledge_base_context()` → 改用 `MultiRecallEngine.recall()` 多路召回
- [x] 8.3 `_build_product_context()` → 改用 `MultiRecallEngine.recall()`
- [x] 8.4 `_generate_chapter_content()` → 注入需求追踪矩阵的约束

## 9. 配置与环境变量

- [x] 9.1 `config/__init__.py` 新增配置项：
  - CHROMA_HOST/PORT（指向 18080）
  - QWEN_EMBEDDING_API_KEY/BASE_URL/MODEL
  - PADDLE_OCR_TOKEN/URL/MODEL
  - MINIO_ENDPOINT/PORT（指向 29000）
- [x] 9.2 `.env` 新增对应密钥配置
- [x] 9.3 删除旧的 CHROMA_HOST=28712 相关配置

## 10. 测试验证

- [x] 10.1 `tests/test_document_parser.py`: 版面解析测试（DOCX 标题检测、PDF 混合解析）
- [x] 10.2 `tests/test_chromadb_client.py`: ChromaDB 直连接口测试
- [x] 10.3 `tests/test_multi_recall.py`: 多路召回 + RRF 融合测试
- [x] 10.4 `tests/test_quality_assurance.py`: 需求追踪 + 后校验测试
- [x] 10.5 运行完整回归测试，确认无 breakage
