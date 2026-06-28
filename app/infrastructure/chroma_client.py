"""ChromaDB REST API 直连客户端。

参考 erp-chromadb/app/chroma.py 的实现。
通过 httpx 直接调用 ChromaDB 的 REST API，不经过外部业务服务。
"""

import logging
import time
from functools import lru_cache
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class ChromaDBClient:
    """直连 ChromaDB REST API 的客户端。

    支持 heartbeat, create/get/delete collection, upsert, get, query, delete。
    自动 provision（tenant/database 不存在时创建）。
    含重试机制。
    """

    def __init__(
        self,
        host: str = "116.63.183.113",
        port: int = 18080,
        ssl: bool = False,
        default_tenant: str = "erp",
        default_database: str = "bidding",
        headers: Optional[dict[str, str]] = None,
        max_retries: int = 2,
        retry_backoff_s: float = 0.3,
        request_timeout_s: float = 30,
        auto_provision: bool = True,
    ):
        self.host = host
        self.port = port
        self.ssl = ssl
        self.default_tenant = default_tenant
        self.default_database = default_database
        self.custom_headers = headers or {}
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self.request_timeout_s = request_timeout_s
        self.auto_provision = auto_provision

    def _base_url(self) -> str:
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"

    def _norm(self, value: str, kind: str = "unknown") -> str:
        """规范化标识符：确保长度 >= 3，否则加前缀。"""
        v = value.strip()
        if len(v) >= 3:
            return v
        if not v:
            return self.default_tenant
        return f"erp_{kind}_{v}"

    def _norm_ctx(
        self, tenant: Optional[str], database: Optional[str], collection: Optional[str] = None
    ) -> tuple[str, str, Optional[str]]:
        t = self._norm(tenant or self.default_tenant, "tenant")
        d = self._norm(database or self.default_database, "database")
        c = self._norm(collection, "collection") if collection is not None else None
        return t, d, c

    def _ensure_tenant_database(self, tenant: str, database: str):
        """确保 tenant/database 存在（auto provision）。"""
        if not self.auto_provision:
            return
        base = self._base_url()
        headers = self.custom_headers

        # 检查 database 是否存在
        def check_db():
            resp = httpx.get(
                f"{base}/api/v1/databases/{database}",
                headers=headers,
                params={"tenant": tenant},
                timeout=self.request_timeout_s,
            )
            return resp

        def create_db():
            resp = httpx.post(
                f"{base}/api/v1/databases",
                headers=headers,
                params={"tenant": tenant},
                json={"name": database},
                timeout=self.request_timeout_s,
            )
            return resp

        try:
            resp = check_db()
            if resp.status_code != 200:
                resp2 = create_db()
                if resp2.status_code not in (200, 201) and resp2.status_code != 409:
                    logger.warning("[chroma] 创建 database 失败: %s %s", resp2.status_code, resp2.text)
        except Exception as exc:
            logger.warning("[chroma] provision 检查异常: %s", exc)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """发送 HTTP 请求到 ChromaDB REST API，含重试。"""
        url = f"{self._base_url()}{path}"
        headers = dict(self.custom_headers)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.request_timeout_s) as client:
                    resp = client.request(method, url, headers=headers, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self.retry_backoff_s * (2**attempt)
                    logger.warning("[chroma] 连接失败，第 %d 次重试 (%ss)", attempt + 1, wait)
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"ChromaDB 连接失败: {exc}") from exc

            if resp.status_code >= 500 and attempt < self.max_retries:
                wait = self.retry_backoff_s * (2**attempt)
                logger.warning("[chroma] HTTP %d, 第 %d 次重试 (%ss)", resp.status_code, attempt + 1, wait)
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise RuntimeError(f"ChromaDB HTTP {resp.status_code}: {resp.text}")

            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                return resp.json()
            return resp.text

        raise RuntimeError(f"ChromaDB 请求最终失败") from last_exc

    # ========== 心跳 ==========

    def heartbeat(self) -> int:
        """检查服务健康状态。"""
        data = self._request("GET", "/api/v1/heartbeat")
        if isinstance(data, dict):
            return int(data.get("nanosecond heartbeat", 0))
        return 0

    # ========== 集合管理 ==========

    def create_collection(
        self, name: str, tenant: Optional[str] = None, database: Optional[str] = None,
        metadata: Optional[dict] = None, get_or_create: bool = True,
    ) -> dict:
        """创建或获取集合。

        返回包含 id 的集合信息 dict。
        """
        tenant, database, name = self._norm_ctx(tenant, database, name)
        if self.auto_provision:
            self._ensure_tenant_database(tenant, database)

        if get_or_create:
            try:
                return self.get_collection(name, tenant, database)
            except RuntimeError as e:
                err_msg = str(e).lower()
                if "404" not in err_msg and "not found" not in err_msg and "does not exist" not in err_msg:
                    raise

        payload: dict[str, Any] = {"name": name}
        if metadata:
            payload["metadata"] = metadata

        result = self._request(
            "POST", "/api/v1/collections",
            params={"tenant": tenant, "database": database},
            json=payload,
        )
        # 如果 POST 返回了包含 id 的 dict，直接使用
        if isinstance(result, dict) and result.get("id"):
            return result
        # 否则回退：重试一次 GET（处理创建后的传播延迟）
        try:
            import time
            time.sleep(0.5)
            return self.get_collection(name, tenant, database)
        except RuntimeError:
            return {"name": name}

    def get_collection(self, name: str, tenant: Optional[str] = None, database: Optional[str] = None) -> dict:
        """获取集合信息。"""
        tenant, database, name = self._norm_ctx(tenant, database, name)
        return self._request(
            "GET", f"/api/v1/collections/{name}",
            params={"tenant": tenant, "database": database},
        )

    def delete_collection(self, name: str, tenant: Optional[str] = None, database: Optional[str] = None):
        """删除集合。"""
        tenant, database, name = self._norm_ctx(tenant, database, name)
        self._request("DELETE", f"/api/v1/collections/{name}", params={"tenant": tenant, "database": database})

    def _collection_id(self, name: str, tenant: Optional[str] = None, database: Optional[str] = None) -> str:
        """获取集合的内部 ID。"""
        info = self.get_collection(name, tenant, database)
        cid = info.get("id") if isinstance(info, dict) else None
        if not cid:
            raise RuntimeError(f"获取 collection ID 失败: {name}")
        return str(cid)

    # ========== 数据操作 ==========

    def upsert(
        self, collection: str, *, ids: list[str], embeddings: Optional[list[list[float]]] = None,
        metadatas: Optional[list[Optional[dict]]] = None, documents: Optional[list[Optional[str]]] = None,
        tenant: Optional[str] = None, database: Optional[str] = None,
    ):
        """写入或更新向量数据。"""
        tenant, database, collection = self._norm_ctx(tenant, database, collection)
        col_info = self.create_collection(collection, tenant, database, get_or_create=True)
        # 优先从 create_collection 返回值取 id，避免立即再 GET
        cid = col_info.get("id") if isinstance(col_info, dict) else None
        if not cid:
            cid = self._collection_id(collection, tenant, database)

        total = len(ids)
        if total == 0:
            return

        BATCH_SIZE = 200
        for start in range(0, total, BATCH_SIZE):
            end = min(start + BATCH_SIZE, total)
            payload: dict[str, Any] = {"ids": ids[start:end]}
            if embeddings is not None:
                payload["embeddings"] = embeddings[start:end]
            if metadatas is not None:
                payload["metadatas"] = metadatas[start:end]
            if documents is not None:
                payload["documents"] = documents[start:end]

            self._request("POST", f"/api/v1/collections/{cid}/upsert", json=payload)

    def get(
        self, collection: str, *, ids: Optional[list[str]] = None,
        where: Optional[dict] = None, include: Optional[list[str]] = None,
        tenant: Optional[str] = None, database: Optional[str] = None,
    ) -> dict:
        """按 ID 或条件获取数据。"""
        tenant, database, collection = self._norm_ctx(tenant, database, collection)
        cid = self._collection_id(collection, tenant, database)
        payload: dict[str, Any] = {}
        if ids is not None:
            payload["ids"] = ids
        if where is not None:
            payload["where"] = where
        if include is not None:
            payload["include"] = include
        return self._request("POST", f"/api/v1/collections/{cid}/get", json=payload)

    def query(
        self, collection: str, *, query_embeddings: list[list[float]],
        n_results: int = 10, where: Optional[dict] = None,
        include: Optional[list[str]] = None,
        tenant: Optional[str] = None, database: Optional[str] = None,
    ) -> dict:
        """向量相似度检索。"""
        tenant, database, collection = self._norm_ctx(tenant, database, collection)
        cid = self._collection_id(collection, tenant, database)
        payload: dict[str, Any] = {
            "query_embeddings": query_embeddings,
            "n_results": n_results,
        }
        if where is not None:
            payload["where"] = where
        if include is not None:
            payload["include"] = include
        return self._request("POST", f"/api/v1/collections/{cid}/query", json=payload)

    def delete(
        self, collection: str, *, ids: Optional[list[str]] = None,
        where: Optional[dict] = None,
        tenant: Optional[str] = None, database: Optional[str] = None,
    ):
        """删除数据。"""
        tenant, database, collection = self._norm_ctx(tenant, database, collection)
        cid = self._collection_id(collection, tenant, database)
        payload: dict[str, Any] = {}
        if ids is not None:
            payload["ids"] = ids
        if where is not None:
            payload["where"] = where
        self._request("POST", f"/api/v1/collections/{cid}/delete", json=payload)
