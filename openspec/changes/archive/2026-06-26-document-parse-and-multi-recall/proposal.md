## Why

当前文档解析和检索架构存在四个核心问题：

1. **文档解析丢失结构**：DOCX/PDF 仅提取纯文本，丢失标题层级、表格结构、版面信息
2. **切片策略粗糙**：按 1200 字机械切割，无视标题边界和表格完整性
3. **依赖外部 Chroma 业务服务**：文件上传到外部服务处理，本地无法控制解析/切片/embedding 策略
4. **单路检索+无出处**：仅向量相似度检索，无关键词召回、无结构化出处溯源
5. **生成质量无保证**：招标要求覆盖率和编造检测完全依赖 LLM 自觉，无硬性校验

## What Changes

### 架构重塑

- **文档解析**：替换为版面感知解析器，DOCX 检测标题层级+表格结构，PDF 混合策略（fitz 文本页 + PaddleOCR 扫描页）
- **ChromaDB 直连**：跳过外部业务服务，本地完成 解析→切片→embedding→写入 ChromaDB REST API
- **MySQL 缓存 + 全文索引**：解析结果缓存到 `doc_parse_cache`，切片写入 `doc_chunks`（含 FULLTEXT ngram 索引）
- **多路召回**：向量检索（语义）+ MySQL FULLTEXT（关键词）→ RRF 融合
- **需求追踪矩阵**：从招标分析阶段开始构建，逐条标注证据状态，生成前后做覆盖率和编造检测

### 存储变更

- **MinIO 统一存储**（`:29000`），禁用本地存储
- **ChromaDB 集合命名**：`tenant=erp` / `database=bidding` / `collection=tender|{公司简称}`
- **MySQL 新增**：`doc_parse_cache`、`doc_chunks` 表，`subject_company.short_name`、`file_storage.file_sha256` 字段

### 质量保证

- 需求追踪矩阵：招标要求逐条提取→绑定章节→标注证据状态
- 生成后校验：逐条检查覆盖率和编造行为

## Capabilities

### New Capabilities

- `layout-aware-parser`: 版面感知的文档解析器（DOCX 标题层级+表格，PDF 视觉识别）
- `direct-chromadb`: 直连 ChromaDB REST API 的适配层
- `multi-recall`: 向量检索 + 关键词检索 + RRF 融合的多路召回引擎
- `quality-assurance`: 需求追踪矩阵 + 生成前后校验的质量保证体系

### Modified Capabilities

- `storage-service`: 改用 MinIO 统一存储，新增文件 SHA256 记录
- `knowledge-base-file-upload`: 改为本地解析→切片→双写入库（ChromaDB + MySQL doc_chunks）

## Impact

- **infrastructure/document_parser.py**: 完全重写，新增版面感知解析逻辑
- **infrastructure/integrations.py**: 重写 ChromaAdapter，删除 HTTP 业务服务调用，改为直连 ChromaDB REST API
- **infrastructure/**: 新增 ocr_client.py、embedding_client.py、chroma_client.py
- **service_modules/chroma_files.py**: 完全重写，改为本地解析→切片→embedding→双写
- **service_modules/task_pipeline/helpers.py**: 改写检索逻辑为多路召回；新增生成前后校验
- **domain/models.py**: 新增 doc_parse_cache、doc_chunks 模型；subject_company 加 short_name；file_storage 加 file_sha256
- **config/__init__.py**: 新增 PINECONE/PaddleOCR/千问 Embedding/MinIO 配置
- **mysql/schema/01_core_schema.sql**: 新增表定义和字段变更
- **API 层 (api/knowledge_bases.py, api/tasks.py)**: 响应结构调整，新增出处字段
- **.env**: 新增配置项
