"""多路召回引擎。

向量检索（ChromaDB）+ 关键词检索（MySQL FULLTEXT）→ RRF 融合 → 带出处的结果。
"""

import json
import logging
import re
from typing import Any, Optional

from flask import current_app

from ..core.extensions import db
from ..domain import DocChunk
from .chroma_client import ChromaDBClient
from .embedding_client import EmbeddingClient

logger = logging.getLogger(__name__)


class RecallResult:
    """单条召回结果。"""

    def __init__(self, text: str, score: float, recall_type: str = "vector",
                 file_id: Optional[int] = None, file_name: str = "",
                 section_path: str = "", content_type: str = "paragraph",
                 chroma_id: str = "", metadata: Optional[dict] = None):
        self.text = text
        self.score = score
        self.recall_type = recall_type  # "vector", "keyword", "both"
        self.file_id = file_id
        self.file_name = file_name
        self.section_path = section_path
        self.content_type = content_type
        self.chroma_id = chroma_id
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "score": round(self.score, 4),
            "recall_type": self.recall_type,
            "source": {
                "file_id": self.file_id,
                "file_name": self.file_name,
                "section_path": self.section_path,
                "content_type": self.content_type,
            },
        }


class MultiRecallEngine:
    """多路召回引擎：向量 + 关键词 + RRF 融合。"""

    RRF_K = 60  # RRF 常数

    def __init__(self, chroma_client: Optional[ChromaDBClient] = None,
                 embedding_client: Optional[EmbeddingClient] = None):
        self._chroma_client = chroma_client
        self._embedding_client = embedding_client
        self._chroma_resolved = chroma_client is not None
        self._embedding_resolved = embedding_client is not None

    @property
    def chroma(self) -> ChromaDBClient:
        if not self._chroma_resolved:
            self._chroma_client = self._default_chroma()
            self._chroma_resolved = True
        return self._chroma_client

    @property
    def embedding(self) -> EmbeddingClient:
        if not self._embedding_resolved:
            self._embedding_client = self._default_embedding()
            self._embedding_resolved = True
        return self._embedding_client

    @staticmethod
    def _default_chroma() -> ChromaDBClient:
        return ChromaDBClient(
            host=current_app.config.get("CHROMA_HOST", "116.63.183.113"),
            port=current_app.config.get("CHROMA_PORT", 18080),
            default_tenant=current_app.config.get("CHROMA_TENANT", "erp"),
            default_database=current_app.config.get("CHROMA_DATABASE", "bidding"),
            max_retries=2,
            auto_provision=True,
        )

    @staticmethod
    def _default_embedding() -> EmbeddingClient:
        return EmbeddingClient(
            api_key=current_app.config.get("QWEN_API_KEY", ""),
            base_url=current_app.config.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=current_app.config.get("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
            max_batch_size=10,
        )

    # ========== 关键词提取（任务 6.5） ==========

    def extract_keywords(self, query: str) -> list[str]:
        """从 query 中提取关键词。

        使用 LLM 提取 + 规则补充。
        """
        keywords = set()

        # 规则提取
        # 1. 引号内的精确短语
        quoted = re.findall(r'"([^"]+)"', query)
        keywords.update(quoted)

        # 2. 标准编号（如 ISO9001, GB/T 12345）
        standards = re.findall(r'[A-Z]{2,}[\d./-]+[A-Z\d]*', query)
        keywords.update(standards)

        # 3. 书名号内的内容
        angle = re.findall(r'《([^》]+)》', query)
        keywords.update(angle)

        # 4. 去掉停用词后的有意义的词
        stop_words = {"的", "了", "是", "在", "有", "和", "就", "不", "也", "都", "要", "而", "与", "及", "或",
                      "一个", "这个", "那个", "可以", "需要", "进行", "通过", "以及", "对于", "关于", "按照",
                      "招标", "文件", "项目", "要求", "投标"}
        parts = re.split(r'[\s,，。；;：:、！!？?（）()【】\[\]{}]', query)
        for part in parts:
            part = part.strip()
            if len(part) >= 2 and part not in stop_words:
                keywords.add(part)

        result = list(keywords)
        # 去重并保留原序
        seen = set()
        ordered = []
        for k in result:
            if k not in seen:
                seen.add(k)
                ordered.append(k)
        return ordered[:10]  # 最多 10 个关键词

    # ========== 向量检索（任务 6.2） ==========

    def _vector_search(self, query: str, collection: str, top_k: int = 20,
                       where: Optional[dict] = None,
                       tenant: str = "erp", database: str = "bidding") -> list[RecallResult]:
        """向量检索。"""
        if not self.embedding.is_available():
            logger.warning("[recall] Embedding 未配置，跳过向量检索")
            return []

        try:
            embeddings = self.embedding.embed_texts([query])
        except Exception as exc:
            logger.warning("[recall] Embedding 失败: %s", exc)
            return []

        try:
            result = self.chroma.query(
                collection,
                query_embeddings=embeddings,
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
                tenant=tenant,
                database=database,
            )
        except Exception as exc:
            logger.warning("[recall] ChromaDB 查询失败: %s", exc)
            return []

        return self._parse_chroma_result(result, "vector")

    def _parse_chroma_result(self, result: dict, recall_type: str) -> list[RecallResult]:
        """解析 ChromaDB 返回结果。"""
        items = []
        documents = result.get("documents", []) if isinstance(result.get("documents"), list) else []
        metadatas = result.get("metadatas", []) if isinstance(result.get("metadatas"), list) else []
        distances = result.get("distances", []) if isinstance(result.get("distances"), list) else []

        for i, doc_list in enumerate(documents):
            meta_list = metadatas[i] if i < len(metadatas) else []
            dist_list = distances[i] if i < len(distances) else []
            for j, doc in enumerate(doc_list if isinstance(doc_list, list) else [doc_list]):
                if not doc or len(str(doc).strip()) < 20:
                    continue
                meta = meta_list[j] if isinstance(meta_list, list) and j < len(meta_list) else (meta_list if isinstance(meta_list, dict) else {})
                dist = dist_list[j] if isinstance(dist_list, list) and j < len(dist_list) else 0.0
                score = 1.0 - float(dist) if dist else 0.5
                items.append(RecallResult(
                    text=str(doc).strip(),
                    score=score,
                    recall_type=recall_type,
                    file_id=meta.get("file_id") if isinstance(meta, dict) else None,
                    file_name=meta.get("file_name", "") if isinstance(meta, dict) else "",
                    section_path=meta.get("section_path", "") if isinstance(meta, dict) else "",
                    content_type=meta.get("content_type", "paragraph") if isinstance(meta, dict) else "paragraph",
                    chroma_id=result.get("ids", [[]])[i][j] if isinstance(result.get("ids"), list) and i < len(result["ids"]) and isinstance(result["ids"][i], list) and j < len(result["ids"][i]) else "",
                    metadata=meta if isinstance(meta, dict) else {},
                ))
        return items

    # ========== 关键词检索（任务 6.3） ==========

    def _keyword_search(self, keywords: list[str], file_id: Optional[int] = None,
                        top_k: int = 20) -> list[RecallResult]:
        """MySQL FULLTEXT 关键词检索。"""
        if not keywords:
            return []

        # 构建 BOOLEAN MODE 查询
        bool_terms = " ".join(f"+{kw}" for kw in keywords if kw.strip())
        if not bool_terms:
            return []

        from sqlalchemy import text as _sa_text
        sql_str = """
            SELECT id, file_id, content, section_path, content_type, chroma_id,
                   MATCH(content) AGAINST(:terms IN BOOLEAN MODE) AS score
            FROM doc_chunks
            WHERE MATCH(content) AGAINST(:terms IN BOOLEAN MODE)
        """
        params = {"terms": bool_terms}

        if file_id is not None:
            sql_str += " AND file_id = :file_id"
            params["file_id"] = file_id

        sql_str += " ORDER BY score DESC LIMIT :limit"
        params["limit"] = top_k

        try:
            rows = db.session.execute(_sa_text(sql_str), params).fetchall()
        except Exception as exc:
            logger.warning("[recall] MySQL FULLTEXT 查询失败: %s", exc)
            return []

        results = []
        for row in rows:
            score = float(row.score) if row.score else 0.0
            if score < 0.001:
                continue
            results.append(RecallResult(
                text=str(row.content),
                score=score,
                recall_type="keyword",
                file_id=row.file_id,
                section_path=row.section_path or "",
                content_type=row.content_type or "paragraph",
                chroma_id=row.chroma_id or "",
            ))
        return results

    # ========== RRF 融合（任务 6.4） ==========

    def _rrf_fusion(self, vector_results: list[RecallResult],
                    keyword_results: list[RecallResult], top_k: int = 15) -> list[RecallResult]:
        """Reciprocal Rank Fusion 融合排序。"""
        scores = {}  # chroma_id -> (score, result)

        for rank, r in enumerate(vector_results):
            key = r.chroma_id or r.text[:100]
            rr = 1.0 / (self.RRF_K + rank + 1)
            if key in scores:
                existing_score, existing = scores[key]
                scores[key] = (existing_score + rr, existing)
                scores[key][1].recall_type = "both"
            else:
                r.score = rr
                scores[key] = (rr, r)

        for rank, r in enumerate(keyword_results):
            key = r.chroma_id or r.text[:100]
            rr = 1.0 / (self.RRF_K + rank + 1)
            if key in scores:
                existing_score, existing = scores[key]
                scores[key] = (existing_score + rr, existing)
                scores[key][1].recall_type = "both"
                scores[key][1].score = existing_score + rr
            else:
                r.score = rr
                scores[key] = (rr, r)

        # 按 score 降序排列
        sorted_items = sorted(scores.values(), key=lambda x: -x[0])
        return [item[1] for item in sorted_items[:top_k]]

    # ========== 统一入口（任务 6.1） ==========

    def recall(self, query: str, collection: str,
               top_k: int = 15, where: Optional[dict] = None,
               tenant: str = "erp", database: str = "bidding",
               file_id: Optional[int] = None) -> list[dict]:
        """多路召回统一入口。

        Args:
            query: 查询文本
            collection: ChromaDB 集合名
            top_k: 最终返回数量
            where: ChromaDB where 过滤条件
            tenant: ChromaDB 租户
            database: ChromaDB 数据库
            file_id: 可选的 MySQL 关键词检索文件过滤

        Returns:
            list[dict]: 融合排序后的结果列表，每条含 text/score/source
        """
        # 1. 关键词提取
        keywords = self.extract_keywords(query)
        logger.info("[recall] 关键词: %s", keywords)

        # 2. 向量检索
        vector_results = self._vector_search(query, collection, top_k=20, where=where, tenant=tenant, database=database)
        logger.info("[recall] 向量检索: %s 条", len(vector_results))

        # 3. 关键词检索
        keyword_results = self._keyword_search(keywords, file_id=file_id, top_k=20)
        logger.info("[recall] 关键词检索: %s 条", len(keyword_results))

        # 4. RRF 融合（任务 6.4）
        fused = self._rrf_fusion(vector_results, keyword_results, top_k=top_k)

        # 5. 格式化结果（任务 6.6）
        return [r.to_dict() for r in fused]
