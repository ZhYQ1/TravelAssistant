"""OpenAI 兼容的 Embedding 客户端。"""

from __future__ import annotations

import math
from typing import Sequence

import httpx

from app.routing.vector import EmbeddingError


class HttpEmbedder:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self._model,
            "input": list(texts),
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings", json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Embedding 请求失败：{exc}") from exc

        if not isinstance(data, dict):
            raise EmbeddingError("Embedding 响应顶层必须是对象")
        items = data.get("data")
        if not isinstance(items, list):
            raise EmbeddingError("Embedding 响应缺少 data 数组")
        if any(not isinstance(item, dict) for item in items):
            raise EmbeddingError("Embedding 响应 data 包含非法项目")
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in ordered]
        if any(
            not isinstance(vector, list)
            or not vector
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            )
            for vector in vectors
        ):
            raise EmbeddingError("Embedding 响应包含空向量")
        if len(vectors) != len(texts):
            raise EmbeddingError("Embedding 返回数量与请求不一致")
        return vectors
