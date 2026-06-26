# Multi-Recall Engine — Specification

## Overview

替换当前单路向量检索，实现"向量语义检索 + MySQL 关键词全文检索"双路召回 + RRF 融合排序。每条结果携带完整出处信息，支持按 file_id/metadata 过滤。

## Architecture

```
Query
  ├─→ LLM 关键词提取 → ["ISO9001", "质量管理体系"]
  ├─→ Vector Search → ChromaDB query(tenant, database, collection, query_embeddings)
  └─→ Keyword Search → MySQL SELECT ... WHERE MATCH(content) AGAINST(... IN BOOLEAN MODE)
                         │
                         └─→ RRF Fusion → Re-rank → Top-N → Format(附出处)
```

## Requirements

### R1: 关键词提取
#### R1.1 提取策略
- SHALL 对用户 query 提取关键术语（专有名词、标准编号、资质名称）
- SHALL 支持 LLM 提取 + 规则补充双保险

#### R1.2 输出格式
- SHALL 返回 `list[str]`，每个元素是一个独立关键词
- SHALL 过滤掉停用词和泛义词

### R2: 向量检索 (Vector Search)
#### R2.1 检索参数
- SHALL 使用千问 embedding 将 query 转为向量
- SHALL 调用 ChromaDB query API
- SHALL 支持 `where` 过滤（按 file_id、knowledge_base_id 等）
- SHALL 默认 `n_results=20`

#### R2.2 返回格式
- SHALL 返回带 metadata 的结构化结果
- SHALL 每条结果含：text, score, source（file_name, section_path, page_range）

### R3: 关键词检索 (Keyword Search)
#### R3.1 检索方式
- SHALL 使用 MySQL FULLTEXT + ngram 分词器
- SHALL 使用 BOOLEAN MODE（支持 `+term` 必须包含、`-term` 排除）
- SHALL 默认取 top_k=20

#### R3.2 查询语句
```sql
SELECT id, file_id, content, section_path, content_type, metadata,
       MATCH(content) AGAINST('+关键词1 +关键词2' IN BOOLEAN MODE) AS score
FROM doc_chunks
WHERE MATCH(content) AGAINST('+关键词1 +关键词2' IN BOOLEAN MODE)
  [AND file_id = ?]  -- 可选过滤
ORDER BY score DESC
LIMIT 20
```

### R4: RRF 融合排序
#### R4.1 融合算法
```python
def rrf_fusion(vector_results, keyword_results, k=60):
    scores = {}
    for rank, item in enumerate(vector_results):
        scores[item["id"]] = scores.get(item["id"], 0) + 1 / (k + rank + 1)
    for rank, item in enumerate(keyword_results):
        scores[item["id"]] = scores.get(item["id"], 0) + 1 / (k + rank + 1)
    # 按 score 降序排列
    return sorted(scores.items(), key=lambda x: -x[1])
```

#### R4.2 去重策略
- SHALL 以 chunk_id 或 content SHA256 为唯一标识去重
- SHALL 融合后保留两路中的最高 score

### R5: 结果格式化
#### R5.1 出处信息
每条结果 SHALL 包含：
```json
{
  "text": "正文内容...",
  "score": 0.92,
  "recall_type": "vector|keyword|both",
  "source": {
    "file_name": "招标文件.pdf",
    "section_path": "第三章 > 3.2 资质要求",
    "page_range": [23, 24],
    "content_type": "paragraph"
  }
}
```

### R6: 业务集成
#### R6.1 招标文件检索
- SHALL 检索 `collection=tender`，按 `file_id` 可选过滤
- SHALL 用于 `_build_tender_chroma_context`

#### R6.2 知识库检索
- SHALL 检索 `collection={subject.short_name}`
- SHALL 按 `knowledge_base_id` + `reference_enabled` 过滤
- SHALL 用于 `_build_knowledge_base_context`

#### R6.3 产品库检索
- SHALL 检索 `collection=product_library`
- SHALL 用于 `_build_product_context`

### R7: 性能
- R7.1 单次召回（向量+关键词+融合）SHALL ≤ 3 秒
- R7.2 MySQL FULLTEXT 查询 SHALL ≤ 100ms（chunk 数 ≤ 10 万条时）

## Scenarios

### Scenario: 同时检索招标文件和知识库
- **GIVEN** 任务关联招标文件 `id=5` 和主体"华铁传媒"
- **WHEN** `recall("第一章 项目概况 技术要求")`
- **THEN** 路1: ChromaDB query(tenant=erp, database=bidding, collection=tender, top_k=20)
- **AND** 路2: MySQL FULLTEXT MATCH AGAINST('+第一章 +项目概况 +技术要求')
- **AND** RRF 融合后取 top_15
- **AND** 每条结果带来源出处

### Scenario: 关键词精确命中
- **GIVEN** 用户需求包含"ISO9001质量管理体系认证"
- **WHEN** LLM 提取关键词 ["ISO9001", "质量管理体系", "质量认证"]
- **THEN** MySQL FULLTEXT: `'+ISO9001 +质量管理体系'` 精确命中包含这些关键词的 chunk
- **AND** 即使向量检索未命中（语义偏差），关键词路仍能召回
