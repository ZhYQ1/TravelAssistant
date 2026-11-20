"""真实服务集成测试：缺配置时整模块跳过，不产生假通过。"""

from __future__ import annotations

import uuid

import pytest

from app.config import ConfigError, Settings

pytestmark = pytest.mark.integration

try:
    SETTINGS = Settings.from_env()
except ConfigError as exc:
    pytest.skip(f"缺少真实服务配置：{exc}", allow_module_level=True)

from app.api.main import build_chain  # noqa: E402
from app.routing.regression import evaluate  # noqa: E402
from app.routing.vector import RedisIntentVectorStore  # noqa: E402
from tests.conftest import load_regression_cases  # noqa: E402


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_redis_vector_store_roundtrip():
    import redis.asyncio as redis_async

    client = redis_async.from_url(SETTINGS.redis_url)
    store = RedisIntentVectorStore(client, SETTINGS.key_prefix, 60)
    namespace = f"itest:{uuid.uuid4().hex}"

    async def scenario():
        await store.save(namespace, {"knowledge_qa|0": [0.1, 0.2, 0.3]})
        return await store.load(namespace)

    loaded = run(scenario())
    assert loaded == {"knowledge_qa|0": [0.1, 0.2, 0.3]}


def test_embedding_dimension_is_stable():
    from app.llm.embedding import HttpEmbedder

    embedder = HttpEmbedder(
        SETTINGS.embedding_base_url,
        SETTINGS.embedding_api_key,
        SETTINGS.embedding_model,
        timeout_seconds=SETTINGS.embedding_timeout_seconds,
    )
    vectors = run(embedder.embed(["出差报销标准", "帮我改行程"]))
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0


def test_l1_route_needs_no_network():
    chain = build_chain(SETTINGS)
    decision = run(chain.route("帮我改行程"))
    assert decision.decision_layer.value == "l1_rule"
    assert decision.route_type.value == "task_coordination"


def test_full_chain_on_clear_utterances():
    chain = build_chain(SETTINGS)
    expected = {
        "不是查政策，帮我改行程": "task_coordination",
        "发票丢了怎么办": "knowledge_qa",
        "提交审批": "workflow",
    }
    for utterance, label in expected.items():
        decision = run(chain.route(utterance))
        assert decision.route_type is not None, (utterance, decision.fallback_reason)
        assert decision.route_type.value == label, (utterance, decision.fallback_reason)


def test_fixed_set_regression_meets_threshold():
    import os

    threshold = float(os.environ.get("REGRESSION_MIN_ACCURACY", "0.8"))
    chain = build_chain(SETTINGS)
    report = run(evaluate(chain, load_regression_cases()))
    print(report.summary())
    assert report.accuracy >= threshold
