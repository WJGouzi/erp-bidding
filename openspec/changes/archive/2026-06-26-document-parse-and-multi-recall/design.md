## Context

### 现状

当前架构：

```
用户上传 → DocumentParser 纯文本提取 → split_text_chunks 粗切片
         → ChromaAdapter (HTTP) → 外部业务服务 (:28712) → ChromaDB
查询阶段 → adapter.query_documents() 单路向量检索 → 拼接 Prompt → LLM
```

核心痛点已在 proposal.md 中描述。

### 相关模块

| 模块 | 行数 | 职责 |
|------|------|------|
| `infrastructure/document_parser.py` | ~570 | 纯文本解析 + 简单切片 |
| `infrastructure/integrations.py` | ~270 | ChromaAdapter HTTP 封装 |
| `service_modules/chroma_files.py` | ~130 | 文件入库 Chroma 逻辑 |
| `service_modules/task_pipeline/helpers.py` | ~1700+ | 知识库检索 + 生成 Prompt 组装 |
| `domain/models.py` | | 数据模型 |
| `config/__init__.py` | | 配置项 |

### 外部依赖

- **ChromaDB**: `116.63.183.113:18080`，REST API `/api/v1/...`
- **MinIO**: `116.63.183.113:29000`
- **通义千问 Embedding**: `dashscope.aliyuncs.com/compatible-mode/v1`，模型 `text-embedding-v4`
- **PaddleOCR**: `paddleocr.aistudio-app.com/api/v2/ocr/jobs`，Token `2d1b0688a3531225c341362ead5b003b745393aa`，模型 `PP-OCRv5`
- **MySQL**: `127.0.0.1:3306`，数据库 `erp_bidding`

## Goals / Non-Goals

**Goals:**
- 版面感知解析：DOCX 保留标题层级+表格结构，PDF 混合策略（文字页+OCR页）
- 语义切片：按标题/表格自然边界切割，每个 chunk 携带完整上下文路径
- 直连 ChromaDB：通过 REST API 直接操作，脱离外部业务服务
- MySQL 全文索引：doc_chunks 表 FULLTEXT ngram 支持关键词检索
- 多路召回：向量 + 关键词 → RRF 融合 → 出处溯源
- 需求追踪矩阵：构建从招标要求到章节的完整追踪链
- 生成前后校验：覆盖率检查 + 编造检测
- 性能目标：50 页混合 PDF 解析 ≤ 15 秒
- MinIO 统一存储，禁用本地存储

**Non-Goals:**
- 不修改现有前端 API 调用方式（只扩展响应字段）
- 不涉及旧数据迁移（ChromaDB 无历史数据）
- 不改动标书生成的编排流程（任务创建→分析→目录→生成 不变）

## Decisions

### D1: PDF 解析策略 — 混合方案
- **方案**: fitz 逐页扫描 → 纯文本页用 fitz 提取，图片/扫描页用 PaddleOCR API 并行识别 → 坐标聚类版面重建
- **替代方案**: 全部走 PaddleOCR → 否决，纯文本页 OCR 慢且浪费
- **替代方案**: 全部走 PyPDF2 → 否决，扫描件无法提取

### D2: ChromaDB 交互方式 — REST API 直连
- **方案**: 参考 `erp-chromadb/app/chroma.py`，用 httpx 直接调用 ChromaDB REST API `/api/v1/...`
- **不采用**: chromadb Python Client（版本兼容风险）
- **不采用**: 继续走外部业务服务（无法控制策略）

### D3: 多路召回融合算法 — RRF
- **方案**: Reciprocal Rank Fusion，`score = 1/(60 + rank)`，两路各 top_k=20，取融合后 top_15
- **替代方案**: 学习加权 → 否决，权重需要大量标注数据

### D4: 关键词检索引擎 — MySQL FULLTEXT ngram
- **方案**: MySQL 8.0 内置 ngram 分词器 FULLTEXT 索引
- **替代方案**: Elasticsearch → 否决，增加运维复杂度，当前数据量级 MySQL 足够

### D5: Embedding 模型 — 通义千问 text-embedding-v4
- **方案**: 通过千问 OpenAI 兼容接口批量 embedding
- **替代方案**: BGE 本地模型 → 否决，GPU 资源未知

### D6: 质量保证 — 需求追踪矩阵 + 后校验
- **方案**: 分析阶段构建 atomic_requirement_items → 目录阶段语义绑定到章节 → 生成后 LLM 逐条校验
- **替代方案**: 仅靠 Prompt 约束 → 否决，不可靠

### D7: 生成后校验方法 — LLM-as-Judge
- **方案**: 用 LLM 对每章检查：每条相关要求是否覆盖、有无编造
- **替代方案**: 规则匹配 → 否决，无法处理语义等价

### D8: PDF OCR 并发策略
- **方案**: `asyncio.gather` 或 `ThreadPoolExecutor` 并发 5 页，总耗时 ≈ 2~3s
- **限制**: 避免 API 限流，不可超过 10 并发

### D9: MySQL 全文索引配置
- **方案**: FULLTEXT INDEX WITH PARSER ngram，最小分词粒度 1 字符
- **注意**: 需 MySQL 8.0+，`ngram_token_size=1`

### D10: Collection 集合管理策略
- **方案**: 
  - 招标文件统一入 `collection=tender`
  - 知识库按主体主体简称入 `collection={subject.short_name}`
  - 生成时同时查两个集合
- **自动创建**: upsert 前调用 create_collection(get_or_create=true)

### D11: 解析缓存失效策略
- **方案**: 基于 `file_sha256` + `parse_version` 双字段判断
- 文件内容变化 → SHA256 变化 → 缓存失效
- 解析器升级 → parse_version 递增 → 旧缓存失效
