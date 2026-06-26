"""通义千问 Embedding API 封装。

支持批量 embedding、自动重试、错误处理。
参考 erp-chromadb/app/embedding.py 的实现。
"""

import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    """封装通义千问 Embedding API 的调用。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "text-embedding-v4",
        max_batch_size: int = 10,
        max_retries: int = 2,
        retry_backoff_s: float = 0.5,
        request_timeout_s: float = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self.request_timeout_s = request_timeout_s

    def is_available(self) -> bool:
        return bool(self.api_key)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本的向量表示。

        Args:
            texts: 文本列表

        Returns:
            list[list[float]]: 向量列表，顺序与输入一致

        Raises:
            EmbeddingError: API 调用失败
        """
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("Embedding API Key 未配置")

        # 如果超出 batch 限制，递归拆分
        if len(texts) > self.max_batch_size:
            results = []
            for i in range(0, len(texts), self.max_batch_size):
                batch = texts[i : i + self.max_batch_size]
                results.extend(self.embed_texts(batch))
            return results

        return self._call_embedding_api(texts)

    def _call_embedding_api(self, texts: list[str]) -> list[list[float]]:
        """调用千问 Embedding API。"""
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "input": texts}

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.request_timeout_s) as client:
                    resp = client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self.retry_backoff_s * (2**attempt)
                    logger.warning("[embedding] 超时，第 %d 次重试 (%ss)", attempt + 1, wait)
                    time.sleep(wait)
                    continue
                raise EmbeddingError(f"Embedding 请求超时（重试 {self.max_retries} 次后）") from exc
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self.retry_backoff_s * (2**attempt)
                    time.sleep(wait)
                    continue
                raise EmbeddingError(f"Embedding 请求失败: {exc}") from exc

            if resp.status_code == 400:
                # 尝试从错误信息中提取 batch 限制
                max_from_err = self._extract_max_batch_size(resp.text)
                if max_from_err and max_from_err < len(texts):
                    logger.info("[embedding] API 返回 batch 限制 %d，递归拆分", max_from_err)
                    results = []
                    for i in range(0, len(texts), max_from_err):
                        batch = texts[i : i + max_from_err]
                        results.extend(self._call_embedding_api(batch))
                    return results

            if resp.status_code >= 500 and attempt < self.max_retries:
                wait = self.retry_backoff_s * (2**attempt)
                logger.warning("[embedding] 服务端错误 %d，第 %d 次重试 (%ss)", resp.status_code, attempt + 1, wait)
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise EmbeddingError(f"Embedding 请求失败: HTTP {resp.status_code} {resp.text}")

            return self._parse_response(resp.json(), len(texts))

        raise EmbeddingError(f"Embedding 请求最终失败") from last_exc

    def _parse_response(self, data: dict, expected_count: int) -> list[list[float]]:
        """解析 API 响应，提取向量。"""
        items = data.get("data", [])
        if not isinstance(items, list):
            raise EmbeddingError("Embedding 返回格式不正确：缺少 data 数组")

        indexed = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            emb = item.get("embedding")
            if isinstance(idx, int) and isinstance(emb, list):
                indexed[idx] = [float(x) for x in emb]

        vectors = []
        for i in range(expected_count):
            v = indexed.get(i)
            if v is None:
                raise EmbeddingError(f"Embedding 返回缺少第 {i} 个向量")
            vectors.append(v)
        return vectors

    @staticmethod
    def _extract_max_batch_size(text: str) -> Optional[int]:
        """从错误信息中提取 batch 大小限制。"""
        m = re.search(r"should not be larger than\s+(\d+)", text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
        return None
