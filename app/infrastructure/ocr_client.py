"""PaddleOCR API 客户端封装。

参考 erp-ocr/ocr-python-service/main.py 的实现。
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class PaddleOCRClient:
    """封装 PaddleOCR 官方 SaaS API 的异步调用。
    
    支持：异步提交任务、轮询结果、多页并发、结果缓存。
    """

    def __init__(
        self,
        token: str = "",
        job_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
        model: str = "PP-OCRv5",
        poll_interval: float = 1.0,
        max_poll_seconds: float = 120,
        request_timeout: float = 30,
        max_concurrency: int = 5,
        cache_enabled: bool = True,
        cache_max_size: int = 128,
        cache_ttl_seconds: int = 600,
    ):
        self.token = token
        self.job_url = job_url
        self.model = model
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds
        self.request_timeout = request_timeout
        self.max_concurrency = max_concurrency
        self.cache_enabled = cache_enabled
        self._cache = OrderedDict()
        self._cache_max_size = cache_max_size
        self._cache_ttl = cache_ttl_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def _build_cache_key(self, image_bytes: bytes) -> str:
        return hashlib.md5(image_bytes).hexdigest()

    async def _get_cache(self, key: str) -> Optional[dict]:
        if not self.cache_enabled:
            return None
        entry = self._cache.get(key)
        if not entry:
            return None
        created_at, value = entry
        if time.time() - created_at > self._cache_ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    async def _put_cache(self, key: str, value: dict):
        if not self.cache_enabled:
            return
        self._cache[key] = (time.time(), value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)

    async def recognize_image(self, image_bytes: bytes) -> list[dict]:
        """识别单张图片，返回文本+坐标列表。
        
        Returns:
            list[dict]: [{"text": "...", "box": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "confidence": 0.99}, ...]
        """
        cache_key = self._build_cache_key(image_bytes)
        cached = await self._get_cache(cache_key)
        if cached is not None:
            return cached

        async with self._semaphore:
            items = await self._call_ocr_api(image_bytes)

        await self._put_cache(cache_key, items)
        return items

    async def _call_ocr_api(self, image_bytes: bytes) -> list[dict]:
        """调用 PaddleOCR 官方 API，异步提交任务并轮询结果。"""
        headers = {"Authorization": f"Bearer {self.token}"}

        async with httpx.AsyncClient(timeout=self.request_timeout) as client:
            # Step 1: 提交任务
            files = {"file": ("image.jpg", image_bytes, "image/jpeg")}
            data = {"model": self.model}
            resp = await client.post(self.job_url, headers=headers, data=data, files=files)
            if resp.status_code >= 400:
                logger.error("[ocr] 提交任务失败: %s %s", resp.status_code, resp.text)
                return []

            result = resp.json()
            job_id = result.get("result", {}).get("jobId") or result.get("jobId")
            if not job_id:
                logger.warning("[ocr] 未获取到 jobId: %s", result)
                return []

            # Step 2: 轮询结果
            poll_url = f"{self.job_url}/{job_id}"
            deadline = time.time() + self.max_poll_seconds
            last_error = None

            while time.time() < deadline:
                await asyncio.sleep(self.poll_interval)
                try:
                    poll_resp = await client.get(poll_url, headers=headers)
                    if poll_resp.status_code >= 400:
                        last_error = f"HTTP {poll_resp.status_code}"
                        continue

                    poll_data = poll_resp.json()
                    status = poll_data.get("status", "")
                    if status in ("done", "completed", "success"):
                        return self._parse_result(poll_data)
                    elif status in ("failed", "error"):
                        logger.error("[ocr] 任务失败: %s", poll_data.get("errorMsg", ""))
                        return []
                    # else: 继续轮询
                except httpx.TimeoutException:
                    last_error = "timeout"
                    continue
                except Exception as exc:
                    last_error = str(exc)
                    continue

            logger.warning("[ocr] 轮询超时 (max=%ss): %s", self.max_poll_seconds, last_error)
            return []

    def _parse_result(self, data: dict) -> list[dict]:
        """从 PaddleOCR 返回结构中提取文本+坐标列表。"""
        items = []
        result_obj = data.get("result", data)
        ocr_results = result_obj.get("ocrResults", []) if isinstance(result_obj, dict) else result_obj if isinstance(result_obj, list) else []

        if isinstance(ocr_results, list):
            for item in ocr_results:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                box = item.get("box") or item.get("boxes")
                confidence = item.get("confidence") or item.get("score", 1.0)
                items.append({
                    "text": text,
                    "box": box if box else None,
                    "confidence": float(confidence) if confidence else 1.0,
                })

        # 按 y 坐标排序（从上到下）
        items.sort(key=lambda x: (x["box"][0][1] if x.get("box") and len(x["box"]) > 0 else 0,
                                   x["box"][0][0] if x.get("box") and len(x["box"]) > 0 else 0))
        return items

    async def recognize_images_batch(self, images: list[bytes]) -> list[list[dict]]:
        """并发识别多张图片。
        
        Args:
            images: 图片字节列表
            
        Returns:
            list[list[dict]]: 每张图片的识别结果列表
        """
        tasks = [self.recognize_image(img) for img in images]
        results = await asyncio.gather(*tasks)
        return results
