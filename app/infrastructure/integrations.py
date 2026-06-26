from minio import Minio
from minio.error import S3Error
from openai import OpenAI


class MinioAdapter:
    """封装 MinIO 文件上传、下载与删除操作。"""

    def __init__(self, endpoint, access_key, secret_key, bucket_name, secure=False):
        """初始化 MinIO 客户端和目标桶配置。"""

        self.bucket_name = bucket_name
        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def ensure_bucket(self):
        """确保目标桶存在，不存在时自动创建。"""

        if not self.client.bucket_exists(self.bucket_name):
            self.client.make_bucket(self.bucket_name)

    def upload_bytes(self, object_name, payload, content_type="application/octet-stream"):
        """将二进制内容上传到 MinIO，并返回对象名。"""

        from io import BytesIO

        self.ensure_bucket()
        self.client.put_object(
            self.bucket_name,
            object_name,
            BytesIO(payload),
            len(payload),
            content_type=content_type,
        )
        return object_name

    def delete_object(self, object_name):
        """删除 MinIO 中的指定对象。"""

        try:
            self.client.remove_object(self.bucket_name, object_name)
            return True
        except S3Error:
            return False

    def download_bytes(self, object_name):
        """下载 MinIO 中指定对象的二进制内容。"""

        response = self.client.get_object(self.bucket_name, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()


class ChromaAdapter:
    """封装 ChromaDB 业务服务（端口 28712）的 REST API 调用。不直连 ChromaDB 数据库。"""

    def __init__(self, host, port=None, tenant=None, database=None):
        """初始化 Chroma 业务服务连接参数。
        Args:
            host: ChromaDB 主机地址（如 116.63.183.113）
            port: 业务服务端口，默认 28712
            tenant: 租户
            database: 数据库
        """
        self.base_url = f"http://{host}:{port or 28712}"
        self.tenant = tenant or "erp"
        self.database = database or "erp"
        self._session = None

    def _get_session(self):
        if self._session is None:
            import httpx
            self._session = httpx.Client(timeout=httpx.Timeout(120.0))
        return self._session

    def _build_params(self, collection=None):
        params = {"tenant": self.tenant, "database": self.database}
        if collection:
            params["collection"] = collection
        return params

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        session = self._get_session()
        resp = session.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"Chroma 服务错误 {resp.status_code}: {resp.text}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Chroma 服务返回错误: {data.get('error', '未知错误')}")
        return data.get("data")

    def _normalize_document_response(self, data):
        if not isinstance(data, dict):
            return data
        nested_result = data.get("result")
        if not isinstance(nested_result, dict):
            return data
        merged = dict(nested_result)
        for key, value in data.items():
            if key != "result" and key not in merged:
                merged[key] = value
        return merged

    def upsert_documents(self, collection_name, documents, ids, metadatas=None):
        """通过业务服务向指定集合写入或更新文档切片。"""
        items = []
        for i, doc_id in enumerate(ids):
            item = {"id": doc_id, "document": documents[i]}
            if metadatas and i < len(metadatas) and metadatas[i]:
                item["metadata"] = metadatas[i]
            items.append(item)
        payload = {"tenant": self.tenant, "database": self.database, "collection": collection_name, "items": items}
        return self._request("POST", "/documents/upsert_json", json=payload)

    def get_documents(self, collection_name, ids, include=None):
        """通过业务服务按 ID 获取文档。"""
        if include is None:
            include = ["documents", "metadatas"]
        payload = {"ids": ids, "include": include}
        params = self._build_params(collection_name)
        data = self._request("POST", "/documents/get", params=params, json=payload)
        return self._normalize_document_response(data)

    def delete_documents(self, collection_name, ids):
        """通过业务服务从指定集合中删除文档。"""
        payload = {"ids": ids}
        params = self._build_params(collection_name)
        return self._request("POST", "/documents/delete", params=params, json=payload)

    def upload_file(self, collection_name, file_content, filename, content_type=None, metadata_json=None):
        """上传文件到 Chroma 业务服务，由服务端处理解析、切片与入库。
        
        对应服务端 POST /documents/upsert（multipart/form-data 文件上传）。
        
        Returns:
            dict: {document_id, sha256, chunks, size_bytes, ...}
        """
        url = f"{self.base_url}/documents/upsert"
        files = {"file": (filename, file_content, content_type or "application/octet-stream")}
        data = {
            "tenant": self.tenant,
            "database": self.database,
            "collection": collection_name,
        }
        if metadata_json:
            data["metadata_json"] = metadata_json
        session = self._get_session()
        resp = session.post(url, data=data, files=files)
        if resp.status_code >= 400:
            raise RuntimeError(f"Chroma 服务上传错误 {resp.status_code}: {resp.text}")
        result = resp.json()
        if not result.get("ok"):
            raise RuntimeError(f"Chroma 服务返回错误: {result.get('error', '未知错误')}")
        return result.get("data")

    def upload_file_async(self, collection_name, file_content, filename, content_type=None, metadata_json=None):
        """异步上传文件到 Chroma 业务服务，立即返回 task_id 和 document_id。
        
        对应服务端 POST /documents/upsert_async（multipart/form-data 文件上传）。
        服务端在后台完成解析、切片与入库，本方法立即返回。
        
        Returns:
            dict: {task_id, document_id, sha256, ...}
        """
        url = f"{self.base_url}/documents/upsert_async"
        files = {"file": (filename, file_content, content_type or "application/octet-stream")}
        data = {
            "tenant": self.tenant,
            "database": self.database,
            "collection": collection_name,
        }
        if metadata_json:
            data["metadata_json"] = metadata_json
        session = self._get_session()
        resp = session.post(url, data=data, files=files)
        if resp.status_code >= 400:
            raise RuntimeError(f"Chroma 服务上传错误 {resp.status_code}: {resp.text}")
        result = resp.json()
        if not result.get("ok"):
            raise RuntimeError(f"Chroma 服务返回错误: {result.get('error', '未知错误')}")
        return result.get("data")

    def get_async_task(self, task_id):
        """查询异步上传任务的状态和结果。
        
        对应服务端 GET /documents/tasks/{task_id}。
        
        Returns:
            dict: {task_id, status, stage, document_id, result, ...} 或 None（任务不存在）
        """
        return self._request("GET", f"/documents/tasks/{task_id}")

    def delete_file_documents(self, collection_name, document_id):
        """按 document_id 删除该文件对应的所有 chunks。
        
        对应服务端 POST /documents/files/delete。
        """
        payload = {"document_id": document_id}
        params = self._build_params(collection_name)
        return self._request("POST", "/documents/files/delete", params=params, json=payload)

    def get_file_documents(self, collection_name, document_id, include=None):
        """按 document_id 获取某个文件的全部 chunks（documents + metadatas）。
        
        对应服务端 POST /documents/files/get。
        """
        if include is None:
            include = ["documents", "metadatas"]
        payload = {"document_id": document_id, "include": include}
        params = self._build_params(collection_name)
        data = self._request("POST", "/documents/files/get", params=params, json=payload)
        return self._normalize_document_response(data)

    def query_documents(self, collection_name, query_texts, top_k=10):
        """通过业务服务进行相似度检索。"""
        payload = {"query_texts": query_texts, "top_k": top_k}
        params = self._build_params(collection_name)
        data = self._request("POST", "/documents/query", params=params, json=payload)
        return self._normalize_document_response(data)

    def get_or_create_collection(self, collection_name, metadata=None):
        """通过业务服务获取或创建集合（由服务端管理，接口层无需显式操作）。"""
        _ = collection_name, metadata
        # upsert_json 内部已自动创建集合，此处仅做兼容占位
        return None


class LLMAdapter:
    """封装 OpenAI 兼容模型服务的文本生成调用。"""

    def __init__(self, api_key, base_url=None, default_model=None):
        """初始化模型客户端配置，未提供密钥时保持不可用状态。"""

        self.api_key = api_key
        self.base_url = base_url or None
        self.default_model = default_model
        self.client = None
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def is_available(self):
        """判断当前是否已具备可调用模型服务的配置。"""

        return self.client is not None

    def generate_text(self, system_prompt, user_prompt, model_name=None, temperature=0.4, max_tokens=1500):
        """调用模型生成文本内容。"""

        if not self.client:
            raise ValueError("未配置 OPENAI_API_KEY，无法调用模型服务")

        response = self.client.chat.completions.create(
            model=model_name or self.default_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        message = response.choices[0].message.content if response.choices else ""
        return (message or "").strip()
