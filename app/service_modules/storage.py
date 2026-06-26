import logging
_logger = logging.getLogger(__name__)

import logging; logger = logging.getLogger(__name__)
import uuid
from pathlib import Path
from ..infrastructure.document_parser import DocumentParser

from flask import current_app
from werkzeug.utils import secure_filename

from ..core.extensions import db
from ..domain import FileStorage
from ..infrastructure.integrations import MinioAdapter


class StorageService:
    """统一处理本地文件与 MinIO 文件的保存、读取和删除。"""

    @staticmethod
    def save_bytes(
        filename,
        payload,
        biz_type,
        chroma_tenant=None,
        chroma_database=None,
        chroma_collection=None,
        content_type="application/octet-stream",
        skip_file_storage=False,
    ):
        """保存二进制内容并返回对应的文件存储记录。
        _logger.debug("[storage] save_bytes biz=%s file=%s size=%s", biz_type, filename, len(payload))

        对于上传文件（skip_file_storage=True），仅创建元数据记录，
        实际文件存储由 ChromaDB 负责。对于生成文件（如标书结果），
        skip_file_storage=False 时仍保存到 MinIO/本地。
        """

        extension = Path(filename).suffix.lower().lstrip(".")
        record = FileStorage(
            biz_type=biz_type,
            file_name=filename,
            file_ext=extension,
            file_size=len(payload),
            storage_provider="CHROMA",
            chroma_tenant=chroma_tenant,
            chroma_database=chroma_database,
            chroma_collection=chroma_collection,
        )

        if not skip_file_storage:
            safe_name = secure_filename(filename) or "file"
            object_name = f"{biz_type.lower()}/{uuid.uuid4().hex}_{safe_name}"
            endpoint = current_app.config.get("MINIO_ENDPOINT")
            access_key = current_app.config.get("MINIO_ACCESS_KEY")
            secret_key = current_app.config.get("MINIO_SECRET_KEY")
            bucket_name = current_app.config.get("MINIO_BUCKET_NAME")
            secure = current_app.config.get("MINIO_SECURE")

            if endpoint and access_key and secret_key and bucket_name:
                adapter = MinioAdapter(endpoint, access_key, secret_key, bucket_name, secure)
                adapter.upload_bytes(object_name, payload, content_type=content_type)
                record.storage_provider = "MINIO"
                record.minio_bucket = bucket_name
                record.minio_object_name = object_name
            else:
                raise RuntimeError(
                    f"MinIO 未配置完整 (endpoint={endpoint}, bucket={bucket_name})，"
                    "无法保存生成的标书文件。请检查 .env 中 MINIO_* 配置。"
                )

        db.session.add(record)
        db.session.flush()
        # 此时 record.id 可用：同步解析并缓存到 doc_parse_cache
        if skip_file_storage and filename:
            try:
                from ..domain import DocParseCache
                import hashlib
                parser = DocumentParser()
                doc = parser.parse_structured(filename, payload)
                parsed_bytes = doc.to_json().encode("utf-8")
                file_sha256 = hashlib.sha256(payload).hexdigest()
                existing = DocParseCache.query.filter_by(file_id=record.id).first()
                if existing:
                    existing.file_sha256 = file_sha256
                    existing.parse_version = "1.0"
                    existing.parsed_json = parsed_bytes
                else:
                    cache_entry = DocParseCache(
                        file_id=record.id,
                        file_sha256=file_sha256,
                        parse_version="1.0",
                        parsed_json=parsed_bytes,
                    )
                    db.session.add(cache_entry)
                db.session.flush()
            except Exception as cache_exc:
                _logger.warning("[storage] 解析缓存失败 file=%s: %s", filename, cache_exc)

        return record

    @staticmethod
    def save_upload(file_storage, biz_type, skip_file_storage=False, chroma_tenant=None, chroma_database=None, chroma_collection=None):
        """读取上传文件对象并复用 save_bytes 完成存储。"""

        original_filename = file_storage.filename or "uploaded_file"
        payload = file_storage.read()
        return StorageService.save_bytes(
            filename=original_filename,
            payload=payload,
            biz_type=biz_type,
            skip_file_storage=skip_file_storage,
            chroma_tenant=chroma_tenant,
            chroma_database=chroma_database,
            chroma_collection=chroma_collection,
            content_type=file_storage.mimetype or "application/octet-stream",
        )

    @staticmethod
    def read_bytes(file_record):
        """读取文件记录对应的二进制内容。"""

        if not file_record:
            return b""
        if file_record.storage_provider in ("CHROMA", "CHROMA_MANAGED"):
            return b""
        if file_record.storage_provider == "MINIO":
            endpoint = current_app.config.get("MINIO_ENDPOINT")
            access_key = current_app.config.get("MINIO_ACCESS_KEY")
            secret_key = current_app.config.get("MINIO_SECRET_KEY")
            bucket_name = current_app.config.get("MINIO_BUCKET_NAME")
            secure = current_app.config.get("MINIO_SECURE")
            adapter = MinioAdapter(endpoint, access_key, secret_key, bucket_name, secure)
            return adapter.download_bytes(file_record.minio_object_name)
        if file_record.local_path and Path(file_record.local_path).exists():
            return Path(file_record.local_path).read_bytes()
        return b""

    @staticmethod
    def read_parsed_text(file_id):
        """从 doc_parse_cache 读取已解析的纯文本内容。
        
        Args:
            file_id: FileStorage 记录 ID
        
        Returns:
            str: 纯文本内容，不存在返回空字符串
        """
        try:
            from ..domain import DocParseCache
            from ..infrastructure.document_parser import StructuredDocument
            import json
            cached = DocParseCache.query.filter_by(file_id=file_id).first()
            if cached and cached.parsed_json:
                doc = StructuredDocument.from_dict(json.loads(cached.parsed_json.decode("utf-8")))
                return doc.to_text()
        except Exception:
            pass
        return ""

    @staticmethod
    def delete_text_cache(file_id):
        """删除指定文件 ID 的解析缓存（doc_parse_cache）。"""
        try:
            from ..domain import DocParseCache
            count = DocParseCache.query.filter_by(file_id=file_id).delete()
            return count > 0
        except Exception:
            pass
        return False

    @staticmethod
    def delete(file_record):
        """删除文件记录关联的实际存储对象并标记逻辑删除。"""

        if not file_record:
            return False
        if file_record.storage_provider in ("CHROMA", "CHROMA_MANAGED"):
            file_record.deleted_flag = True
            return True
        if file_record.storage_provider == "MINIO" and file_record.minio_object_name:
            endpoint = current_app.config.get("MINIO_ENDPOINT")
            access_key = current_app.config.get("MINIO_ACCESS_KEY")
            secret_key = current_app.config.get("MINIO_SECRET_KEY")
            bucket_name = current_app.config.get("MINIO_BUCKET_NAME")
            secure = current_app.config.get("MINIO_SECURE")
            if endpoint and access_key and secret_key and bucket_name:
                MinioAdapter(endpoint, access_key, secret_key, bucket_name, secure).delete_object(
                    file_record.minio_object_name
                )
        elif file_record.local_path:
            Path(file_record.local_path).unlink(missing_ok=True)
        file_record.deleted_flag = True
        return True
