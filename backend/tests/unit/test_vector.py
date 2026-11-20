import asyncio

import pytest

from app.models import RouteType
from app.routing.vector import (
    DEFAULT_EXEMPLARS,
    InMemoryIntentVectorStore,
    VectorRouter,
    cosine,
)

TASK = [1.0, 0.0, 0.0]
KNOWLEDGE = [0.0, 1.0, 0.0]
WORKFLOW = [0.0, 0.0, 1.0]

EXEMPLARS = {
    RouteType.TASK_COORDINATION: ("改一下行程",),
    RouteType.KNOWLEDGE_QA: ("报销标准",),
    RouteType.WORKFLOW: ("发起审批",),
}


def run(coro):
    return asyncio.run(coro)


class FakeEmbedder:
    """按文本给出固定向量，并记录调用次数。"""

    def __init__(self, mapping: dict[str, list[float]], default: list[float] | None = None):
        self.mapping = mapping
        self.default = default or [0.0, 0.0, 0.0]
        self.batch_calls = 0
        self.text_calls = 0
        self.fail = False

    async def embed(self, texts):
        self.batch_calls += 1
        self.text_calls += len(texts)
        if self.fail:
            raise RuntimeError("embedding down")
        return [self.mapping.get(text, self.default) for text in texts]

    def for_exemplars(self):
        mapping = dict(self.mapping)
        mapping.update({
            "改一下行程": TASK,
            "报销标准": KNOWLEDGE,
            "发起审批": WORKFLOW,
        })
        self.mapping = mapping
        return self


def make_router(embedder, store=None, **overrides):
    options = dict(
        model_name="test-embed",
        min_score=0.62,
        margin=0.05,
        exemplars=EXEMPLARS,
    )
    options.update(overrides)
    return VectorRouter(embedder, store or InMemoryIntentVectorStore(), **options)


def test_cosine_handles_zero_and_dimension_mismatch():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([0, 0], [1, 0]) == 0.0
    with pytest.raises(ValueError):
        cosine([1, 0], [1, 0, 0])


def test_clear_winner_decides():
    embedder = FakeEmbedder({"改早一点行程": TASK}).for_exemplars()
    result = run(make_router(embedder).recall("改早一点行程"))
    assert result.status == "decided"
    assert result.intent is RouteType.TASK_COORDINATION
    assert result.confidence == pytest.approx(1.0)
    assert len(result.candidates) == 3


def test_tie_below_margin_stays_undecided():
    embedder = FakeEmbedder({"模棱两可": [0.7071, 0.7071, 0.0]}).for_exemplars()
    result = run(make_router(embedder).recall("模棱两可"))
    assert result.status == "undecided"
    assert "区分度" in result.reason


def test_low_score_stays_undecided():
    embedder = FakeEmbedder({"都不太像": [0.6, 0.6, 0.6]}).for_exemplars()
    result = run(make_router(embedder).recall("都不太像"))
    assert result.status == "undecided"
    assert "低于下限" in result.reason


def test_embedding_failure_degrades_to_error():
    embedder = FakeEmbedder({}).for_exemplars()
    embedder.fail = True
    result = run(make_router(embedder).recall("随便"))
    assert result.status == "error"
    assert "embedding down" in result.reason


def test_index_is_cached_and_written_once():
    embedder = FakeEmbedder({"改早一点行程": TASK}).for_exemplars()
    store = InMemoryIntentVectorStore()
    router = make_router(embedder, store)

    run(router.recall("改早一点行程"))
    run(router.recall("改早一点行程"))

    assert store.save_count == 1
    assert embedder.text_calls == len(EXEMPLARS) + 2
    assert router.namespace == "v1:test-embed"


def test_model_change_invalidates_namespace():
    embedder = FakeEmbedder({}).for_exemplars()
    store = InMemoryIntentVectorStore()
    first = make_router(embedder, store, model_name="model-a")
    second = make_router(embedder, store, model_name="model-b")
    run(first.ensure_index())
    run(second.ensure_index())
    assert first.namespace != second.namespace
    assert store.save_count == 2


def test_default_exemplars_cover_all_route_types():
    assert set(DEFAULT_EXEMPLARS) == set(RouteType)
    assert all(len(samples) >= 5 for samples in DEFAULT_EXEMPLARS.values())
