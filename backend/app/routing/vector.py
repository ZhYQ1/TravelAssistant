"""L2 向量召回：真实 Embedding 服务 + Redis 持久化意图示例向量。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence

from app.models import Candidate, RouteType

DEFAULT_EXEMPLARS: dict[RouteType, tuple[str, ...]] = {
    RouteType.TASK_COORDINATION: (
        "帮我规划一下下周去上海的出差行程",
        "把明天的行程改早一点",
        "这个酒店不合适，换一个",
        "周五的航班改签一下",
        "给我排一个三天的出差安排",
        "重新安排一下回程时间",
        "确认这个行程没问题",
        "行程里加一天会议时间",
    ),
    RouteType.KNOWLEDGE_QA: (
        "出差住宿的报销标准是多少",
        "高铁二等座可以报销吗",
        "差旅政策里对机票舱位怎么规定的",
        "出差发票需要哪些材料",
        "去深圳出差有什么注意事项",
        "这个城市住哪个区域比较方便",
        "公司差旅制度对餐补怎么规定的",
        "能不能带家属一起出差",
    ),
    RouteType.WORKFLOW: (
        "帮我发起出差审批",
        "把审批流程走一下",
        "提交报销单",
        "办理一下报销申请",
        "出差审批在哪里提交",
        "帮我把这个出差申请提审",
        "走流程申请下周出差",
        "提交一下差旅审批",
    ),
}


class EmbeddingError(RuntimeError):
    """Embedding 服务不可用或返回非预期结构。"""


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把文本批量转为稠密向量。"""


class IntentVectorStore(Protocol):
    async def load(self, namespace: str) -> dict[str, list[float]] | None:
        """读取已缓存的示例向量；不存在或版本不符时返回 None。"""

    async def save(self, namespace: str, vectors: dict[str, list[float]]) -> None:
        """写入示例向量并设置生命周期。"""


class RedisIntentVectorStore:
    """以 Hash 保存向量：field 为示例键，value 为 JSON 向量。"""

    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, namespace: str) -> str:
        return f"{self._prefix}:intent_vectors:{namespace}"

    async def load(self, namespace: str) -> dict[str, list[float]] | None:
        raw = await self._redis.hgetall(self._key(namespace))
        if not raw:
            return None
        return {
            (key.decode() if isinstance(key, bytes) else key): json.loads(value)
            for key, value in raw.items()
        }

    async def save(self, namespace: str, vectors: dict[str, list[float]]) -> None:
        key = self._key(namespace)
        payload = {name: json.dumps(vector) for name, vector in vectors.items()}
        await self._redis.hset(key, mapping=payload)
        await self._redis.expire(key, self._ttl)


class InMemoryIntentVectorStore:
    """与 Redis 行为一致的本地实现，用于单测与离线开发。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, list[float]]] = {}
        self.save_count = 0

    async def load(self, namespace: str) -> dict[str, list[float]] | None:
        return self._data.get(namespace)

    async def save(self, namespace: str, vectors: dict[str, list[float]]) -> None:
        self._data[namespace] = dict(vectors)
        self.save_count += 1


@dataclass(frozen=True)
class L2Result:
    status: Literal["decided", "undecided", "error"]
    intent: RouteType | None = None
    confidence: float = 0.0
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    reason: str = ""


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("向量维度不一致")
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


class VectorRouter:
    """L2：对意图示例向量做余弦召回，仅在区分度足够时直接生效。"""

    def __init__(
        self,
        embedder: Embedder,
        store: IntentVectorStore,
        *,
        model_name: str,
        min_score: float,
        margin: float,
        exemplars: dict[RouteType, tuple[str, ...]] | None = None,
        index_version: str = "v1",
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._model_name = model_name
        self._min_score = min_score
        self._margin = margin
        self._exemplars = exemplars or DEFAULT_EXEMPLARS
        self._namespace = f"{index_version}:{model_name}"
        self._vectors: dict[str, list[float]] | None = None

    @property
    def namespace(self) -> str:
        return self._namespace

    async def ensure_index(self) -> None:
        """索引缺失时按当前模型构建并写回 Redis。"""
        if self._vectors is not None:
            return
        cached = await self._store.load(self._namespace)
        if cached:
            self._vectors = cached
            return

        names: list[str] = []
        texts: list[str] = []
        for intent, samples in self._exemplars.items():
            for index, sample in enumerate(samples):
                names.append(f"{intent.value}|{index}")
                texts.append(sample)
        vectors = await self._embedder.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError("示例向量数量与文本数量不一致")
        built = dict(zip(names, vectors))
        await self._store.save(self._namespace, built)
        self._vectors = built

    async def recall(self, text: str) -> L2Result:
        try:
            await self.ensure_index()
            query = (await self._embedder.embed([text]))[0]
        except Exception as exc:  # noqa: BLE001 - 统一降级给上层链路
            return L2Result("error", reason=f"{type(exc).__name__}: {exc}")

        assert self._vectors is not None
        best: dict[RouteType, float] = {}
        for name, vector in self._vectors.items():
            intent = RouteType(name.split("|", 1)[0])
            score = cosine(query, vector)
            if score > best.get(intent, -1.0):
                best[intent] = score

        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        candidates = tuple(
            Candidate(route_type=intent, score=round(score, 6)) for intent, score in ranked
        )
        if not candidates:
            return L2Result("undecided", reason="索引为空")
        if candidates[0].score < self._min_score:
            return L2Result(
                "undecided",
                candidates=candidates,
                reason=f"最高分 {candidates[0].score:.3f} 低于下限 {self._min_score}",
            )
        gap = candidates[0].score - (candidates[1].score if len(candidates) > 1 else 0.0)
        if gap < self._margin:
            return L2Result(
                "undecided",
                candidates=candidates,
                reason=f"top1/top2 区分度 {gap:.3f} 低于 {self._margin}",
            )
        return L2Result(
            "decided",
            intent=candidates[0].route_type,
            confidence=candidates[0].score,
            candidates=candidates,
            reason=f"区分度 {gap:.3f} 满足条件",
        )
