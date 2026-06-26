"""文档入库 ChromaDB 模块 — 新版（直连 ChromaDB）。

流程: 解析 → 切片 → Embedding → 双写入库（ChromaDB + MySQL doc_chunks）
"""

import hashlib
import json
import logging
from typing import Any, Callable, Optional

from flask import current_app

from ..core.extensions import db
from ..domain import DocChunk, DocParseCache, FileStorage
from ..infrastructure.chroma_client import ChromaDBClient
from ..infrastructure.document_parser import DocumentParser
from ..infrastructure.embedding_client import EmbeddingClient
from .common import log_operation

logger = logging.getLogger(__name__)

PARSE_VERSION = "1.0"


def _get_chroma_client() -> ChromaDBClient:
    return ChromaDBClient(
        host=current_app.config.get("CHROMA_HOST", "116.63.183.113"),
        port=current_app.config.get("CHROMA_PORT", 18080),
        ssl=current_app.config.get("CHROMA_SSL", False),
        default_tenant=current_app.config.get("CHROMA_TENANT", "erp"),
        default_database=current_app.config.get("CHROMA_DATABASE", "bidding"),
        max_retries=current_app.config.get("CHROMA_MAX_RETRIES", 2),
        auto_provision=True,
    )


def _get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(
        api_key=current_app.config.get("QWEN_API_KEY", ""),
        base_url=current_app.config.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model=current_app.config.get("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        max_batch_size=10,
    )


def _get_parser() -> DocumentParser:
    return DocumentParser()


def _compute_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _check_parse_cache(file_id: int, file_sha256: str) -> Optional[bytes]:
    """检查解析缓存，命中返回 parsed_json，否则返回 None。"""
    cached = DocParseCache.query.filter_by(file_id=file_id).first()
    if cached and cached.file_sha256 == file_sha256 and cached.parse_version == PARSE_VERSION:
        logger.info("[chroma] 解析缓存命中: file_id=%s", file_id)
        return cached.parsed_json
    return None


def _save_parse_cache(file_id: int, file_sha256: str, parsed_json: bytes, chunk_count: int):
    """保存解析缓存。"""
    existing = DocParseCache.query.filter_by(file_id=file_id).first()
    if existing:
        existing.file_sha256 = file_sha256
        existing.parse_version = PARSE_VERSION
        existing.parsed_json = parsed_json
        existing.chunk_count = chunk_count
    else:
        cache = DocParseCache(
            file_id=file_id,
            file_sha256=file_sha256,
            parse_version=PARSE_VERSION,
            parsed_json=parsed_json,
            chunk_count=chunk_count,
        )
        db.session.add(cache)
    db.session.flush()


def _delete_doc_chunks(file_id: int):
    """删除指定文件的所有切片记录。"""
    DocChunk.query.filter_by(file_id=file_id).delete()
    db.session.flush()


def _save_doc_chunks(file_id: int, chunks: list[dict], chroma_ids: list[str]):
    """保存切片到 MySQL doc_chunks 表（含全文索引）。"""
    _delete_doc_chunks(file_id)
    for i, (chunk, chroma_id) in enumerate(zip(chunks, chroma_ids)):
        doc_chunk = DocChunk(
            file_id=file_id,
            chunk_index=i,
            content=chunk["text"],
            section_path=chunk.get("section_path", ""),
            content_type=chunk.get("content_type", "paragraph"),
            extra_metadata=json.dumps(chunk.get("metadata", {}), ensure_ascii=False) if chunk.get("metadata") else None,
            chroma_id=chroma_id,
        )
        db.session.add(doc_chunk)
    db.session.flush()


def ingest_file_to_chroma(
    file_record: FileStorage,
    filename: str,
    payload: bytes,
    chunk_id_prefix: str = "chunk",
    metadata_builder: Optional[Callable] = None,
    chroma_tenant: Optional[str] = None,
    chroma_database: Optional[str] = None,
    chroma_collection: Optional[str] = None,
):
    """新版入库：解析 → 切片 → embedding → 双写入库。

    Args:
        file_record: FileStorage 记录
        filename: 文件名
        payload: 文件二进制内容
        chunk_id_prefix: chunk ID 前缀
        metadata_builder: 可选，接收 (index, chunk) 返回 dict
        chroma_tenant: Chroma 租户，默认 "erp"
        chroma_database: Chroma 数据库，默认 "bidding"
        chroma_collection: Chroma 集合，默认按文件类型自动选择
    """
    logger.info("[chroma] 开始入库: file=%s", filename)

    if current_app.config.get("TESTING"):
        logger.info("[chroma] TESTING 模式，跳过入库: file=%s", filename)
        return None

    # 确定目标集合
    tenant = chroma_tenant or current_app.config.get("CHROMA_TENANT", "erp")
    database = chroma_database or current_app.config.get("CHROMA_DATABASE", "bidding")
    collection = chroma_collection or current_app.config.get("CHROMA_COLLECTION", "tender")

    # 计算 SHA256
    file_sha256 = _compute_sha256(payload)
    file_record.file_sha256 = file_sha256

    # 检查解析缓存（任务 5.5）
    cached_json = _check_parse_cache(file_record.id, file_sha256) if file_record.id else None

    if cached_json:
        # 缓存命中：直接反序列化
        from ..infrastructure.document_parser import StructuredDocument
        doc = StructuredDocument.from_dict(json.loads(cached_json.decode("utf-8")))
        logger.info("[chroma] 使用缓存解析结果: file=%s", filename)
    else:
        # 解析文档
        parser = _get_parser()
        doc = parser.parse_structured(filename, payload, file_sha256)

        # 保存解析缓存（任务 5.4）
        if file_record.id:
            parsed_bytes = doc.to_json().encode("utf-8")
            _save_parse_cache(file_record.id, file_sha256, parsed_bytes, 0)

    # 语义切片
    parser = _get_parser()
    chunks = parser.semantic_chunk(doc)
    logger.info("[chroma] 切片完成: file=%s, chunks=%s", filename, len(chunks))

    if not chunks:
        logger.warning("[chroma] 无切片内容: file=%s", filename)
        return None

    # 构建 metadata
    metadatas = []
    for i, chunk in enumerate(chunks):
        meta = {
            "file_id": file_record.id,
            "file_name": filename,
            "chunk_index": i,
            "section_path": chunk.get("section_path", ""),
            "content_type": chunk.get("content_type", "paragraph"),
        }
        if metadata_builder:
            extra = metadata_builder(i, chunk)
            if extra:
                meta.update(extra)
        metadatas.append(meta)

    # Embedding
    embed_client = _get_embedding_client()
    if not embed_client.is_available():
        logger.warning("[chroma] Embedding 客户端未配置，跳过: file=%s", filename)
        return None

    texts = [chunk["text"] for chunk in chunks]
    try:
        embeddings = embed_client.embed_texts(texts)
        logger.info("[chroma] Embedding 完成: file=%s, vectors=%s", filename, len(embeddings))
    except Exception as exc:
        logger.error("[chroma] Embedding 失败: %s", exc)
        return None

    # 生成 ChromaDB IDs
    chroma_ids = [f"{chunk_id_prefix}_{file_record.id}_{i}" for i in range(len(chunks))]

    # 写入 ChromaDB
    try:
        chroma_client = _get_chroma_client()
        chroma_client.upsert(
            collection,
            ids=chroma_ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=texts,
            tenant=tenant,
            database=database,
        )
        logger.info("[chroma] ChromaDB 写入完成: file=%s, collection=%s", filename, collection)
    except Exception as exc:
        logger.error("[chroma] ChromaDB 写入失败: %s", exc)
        return None

    # 写入 MySQL doc_chunks
    if file_record.id:
        try:
            _save_doc_chunks(file_record.id, chunks, chroma_ids)
            # 更新缓存中的切片数
            cached = DocParseCache.query.filter_by(file_id=file_record.id).first()
            if cached:
                cached.chunk_count = len(chunks)
            db.session.commit()
            logger.info("[chroma] MySQL doc_chunks 写入完成: file=%s, chunks=%s", filename, len(chunks))
        except Exception as exc:
            logger.error("[chroma] MySQL doc_chunks 写入失败: %s", exc)
            db.session.rollback()

    # 更新 file_record
    file_record.chroma_tenant = tenant
    file_record.chroma_database = database
    file_record.chroma_collection = collection
    file_record.chroma_doc_id = json.dumps(chroma_ids, ensure_ascii=False)

    log_operation(
        module="chroma",
        action="ingest_documents",
        target_type="FileStorage",
        target_id=file_record.id,
        summary=f"写入 Chroma 向量库: {filename} (共{len(chunks)}个切片)",
        detail={
            "file_id": file_record.id,
            "file_name": filename,
            "chunk_count": len(chunks),
            "collection": collection,
            "chunk_ids_preview": chroma_ids[:3],
        },
    )

    return {
        "document_id": chroma_ids[0] if chroma_ids else None,
        "chunk_count": len(chunks),
        "chroma_ids": chroma_ids,
    }


def delete_file_chroma_documents(
    file_record: FileStorage,
    chroma_tenant: Optional[str] = None,
    chroma_database: Optional[str] = None,
    chroma_collection: Optional[str] = None,
):
    """从 ChromaDB 和 MySQL 中删除文件的所有向量数据。"""
    if not file_record or current_app.config.get("TESTING"):
        return False

    tenant = chroma_tenant or file_record.chroma_tenant or current_app.config.get("CHROMA_TENANT", "erp")
    database = chroma_database or file_record.chroma_database or current_app.config.get("CHROMA_DATABASE", "bidding")
    collection = chroma_collection or file_record.chroma_collection or current_app.config.get("CHROMA_COLLECTION", "tender")

    # 从 MySQL doc_chunks 删除
    if file_record.id:
        try:
            _delete_doc_chunks(file_record.id)
            # 删除解析缓存
            DocParseCache.query.filter_by(file_id=file_record.id).delete()
            db.session.flush()
            logger.info("[chroma] MySQL 删除完成: file_id=%s", file_record.id)
        except Exception as exc:
            logger.warning("[chroma] MySQL 删除失败: %s", exc)
            db.session.rollback()

    # 从 ChromaDB 删除
    try:
        chroma_client = _get_chroma_client()
        chroma_client.delete(
            collection,
            where={"file_id": {"$in": [file_record.id]}} if file_record.id else None,
            tenant=tenant,
            database=database,
        )
        logger.info("[chroma] ChromaDB 删除完成: file=%s, collection=%s", file_record.file_name, collection)
    except Exception as exc:
        logger.warning("[chroma] ChromaDB 删除失败: %s", exc)

    log_operation(
        module="chroma",
        action="delete_documents",
        target_type="FileStorage",
        target_id=file_record.id,
        summary=f"删除 Chroma 向量: {file_record.file_name}",
        detail={"file_id": file_record.id, "file_name": file_record.file_name, "collection": collection},
    )

    return True
