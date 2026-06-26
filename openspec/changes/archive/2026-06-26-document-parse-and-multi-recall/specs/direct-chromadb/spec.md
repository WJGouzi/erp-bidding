# Direct ChromaDB — Specification

## Overview

替代当前通过外部业务服务（:28712）操作 ChromaDB 的方式，改为直连 ChromaDB REST API（:18080）。本地完成解析→切片→embedding→写入的全流程控制。

## Architecture

```
chunks → Qwen Embedding API → embeddings
                            → ChromaDB REST API (:18080) /api/v1/...
                                  → upsert(ids, embeddings, documents, metadatas)
                            → MySQL doc_chunks (全文索引)
```

## Requirements

### R1: ChromaDBClient
#### R1.1 基础操作
- SHALL 通过 httpx 调用 ChromaDB REST API
- SHALL 实现：heartbeat, create_collection, get_collection, delete_collection, list_collections
- SHALL 实现：upsert, get, query, delete

#### R1.2 自动 Provision
- SHALL upsert 前确保 tenant/database 存在（GET /api/v1/databases/{db}，不存在则创建）
- SHALL 遵循 `_norm_ctx` 规范化规则：标识符 < 3 字符时补前缀 `erp_{kind}_`

#### R1.3 重试机制
- SHALL 对可重试错误自动重试（超时、连接错误、502/503/504）
- SHALL 指数退避：`0.3s * 2^attempt`，最多 2 次重试

#### R1.4 批量写入
- SHALL upsert 支持分批（每批 ≤ 200 条）
- SHALL 支持 metadata 中的复杂类型自动序列化

### R2: EmbeddingClient
#### R2.1 千问 Embedding
- SHALL 调用 `https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings`
- SHALL 使用模型 `text-embedding-v4`
- SHALL 通过 `Authorization: Bearer {api_key}` 鉴权

#### R2.2 批量 Embedding
- SHALL 支持一次传入多个文本（batch）
- SHALL 对超出 API 限制的 batch 自动递归拆分

#### R2.3 错误处理
- SHALL 对 429/5xx 自动重试（指数退避）
- SHALL 对非重试错误（4xx 非 429）直接抛出

### R3: 文件入库流程
#### R3.1 招标文件入库
- SHALL 解析→切片→embedding 后写入 `collection=tender`
- SHALL metadata 包含：file_id, section_path, chunk_index, page_range, content_type
- SHALL 写入前先删除该 file_id 的旧数据（覆盖上传场景）

#### R3.2 知识库文件入库
- SHALL 根据 `SubjectCompany.short_name` 确定 collection 名称
- SHALL metadata 额外包含：knowledge_base_id, reference_enabled

#### R3.3 删除流程
- SHALL 支持按 `where: {file_id: {"$in": [...]}}` 批量删除
- SHALL 同步删除 MySQL `doc_chunks` 中的对应记录
- SHALL 删除 `doc_parse_cache` 中的缓存

### R4: 集合命名规则
- tenant: `"erp"`（固定）
- database: `"bidding"`（固定）
- collection: `"tender"`（招标文件）或 `SubjectCompany.short_name`（知识库）
- SHALL 规范化后长度 ≥ 3 字符

## Scenarios

### Scenario: 上传招标文件并入库
- **WHEN** 用户上传招标文件
- **THEN** 文件解析后切片（N 个 chunk）
- **AND** 千问批量 embedding 所有 chunk
- **AND** ChromaDB upsert 到 `collection=tender`，batch_size=200
- **AND** MySQL doc_chunks 写入 N 条记录
- **AND** doc_parse_cache 写入解析缓存

### Scenario: 上传知识库文件并入库
- **GIVEN** 主体"华铁传媒"的 short_name = "华铁传媒"
- **WHEN** 用户向该主体知识库上传文件
- **THEN** ChromaDB upsert 到 `collection=华铁传媒`
- **AND** metadata 携带 knowledge_base_id
- **AND** MySQL doc_chunks 记录携带 kb_id

### Scenario: 删除知识库文件
- **WHEN** 用户删除知识库中的某个文件
- **THEN** ChromaDB 删除 `where: {file_id: {"$in": [id]}}`
- **AND** MySQL 删除对应 doc_chunks 记录
- **AND** MySQL 删除 doc_parse_cache 记录
