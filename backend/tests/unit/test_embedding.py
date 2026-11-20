import asyncio
import json

import httpx
import pytest

from app.llm.embedding import HttpEmbedder
from app.routing.vector import EmbeddingError


def run(coro):
    return asyncio.run(coro)


def test_siliconflow_embedding_request_uses_model_auth_and_float_output():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "object": "list",
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ],
        })

    embedder = HttpEmbedder(
        "https://api.siliconflow.cn/v1",
        "unit-test-key",
        "BAAI/bge-m3",
        transport=httpx.MockTransport(handler),
    )

    vectors = run(embedder.embed(["第一条", "第二条"]))

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    request = requests[0]
    assert str(request.url) == "https://api.siliconflow.cn/v1/embeddings"
    assert request.headers["authorization"] == "Bearer unit-test-key"
    assert json.loads(request.content) == {
        "model": "BAAI/bge-m3",
        "input": ["第一条", "第二条"],
        "encoding_format": "float",
    }


@pytest.mark.parametrize(
    "body,content",
    [
        ([], None),
        ({"data": [{"index": 0, "embedding": []}]}, None),
        (None, b'{"data":[{"index":0,"embedding":[NaN]}]}'),
        ({"data": ["invalid"]}, None),
    ],
)
def test_embedding_rejects_malformed_or_non_finite_vectors(body, content):
    def handler(request):
        if content is not None:
            return httpx.Response(200, content=content, headers={"Content-Type": "application/json"})
        return httpx.Response(200, json=body)

    embedder = HttpEmbedder(
        "https://api.siliconflow.cn/v1",
        "unit-test-key",
        "BAAI/bge-m3",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmbeddingError):
        run(embedder.embed(["测试文本"]))


def test_embedding_http_error_surfaces_as_embedding_error():
    embedder = HttpEmbedder(
        "https://api.siliconflow.cn/v1",
        "unit-test-key",
        "BAAI/bge-m3",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    with pytest.raises(EmbeddingError, match="Embedding 请求失败"):
        run(embedder.embed(["测试文本"]))
