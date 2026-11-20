"""SkillContentCollapseHook 与静态/动态 Prompt 解耦。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


class ContentNotRecoverable(RuntimeError):
    """折叠引用已失效；调用方必须降级，不能虚构原 Skill 输出。"""


@dataclass(frozen=True)
class SkillContent:
    content_id: str
    skill_id: str
    loaded_turn: int
    text: str
    summary: str = ""
    active: bool = False
    pending_tool_call: bool = False


@dataclass(frozen=True)
class FoldedSkillContent:
    content_id: str
    skill_id: str
    loaded_turn: int
    summary: str
    reference: str


@dataclass(frozen=True)
class CollapseResult:
    blocks: tuple[SkillContent | FoldedSkillContent, ...]
    folded_count: int
    folded_tokens: int

    @property
    def folded_ids(self) -> tuple[str, ...]:
        return tuple(
            block.content_id for block in self.blocks
            if isinstance(block, FoldedSkillContent)
        )


class SkillContentStore(Protocol):
    async def save(self, content: SkillContent) -> None: ...
    async def get(self, content_id: str) -> SkillContent | None: ...


class InMemorySkillContentStore:
    def __init__(self) -> None:
        self._contents: dict[str, SkillContent] = {}

    async def save(self, content: SkillContent) -> None:
        self._contents[content.content_id] = content

    async def get(self, content_id: str) -> SkillContent | None:
        return self._contents.get(content_id)


class RedisSkillContentStore:
    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, content_id: str) -> str:
        return f"{self._prefix}:skill_content:{content_id}"

    async def save(self, content: SkillContent) -> None:
        from dataclasses import asdict

        await self._redis.set(
            self._key(content.content_id), json.dumps(asdict(content), ensure_ascii=False),
            ex=self._ttl,
        )

    async def get(self, content_id: str) -> SkillContent | None:
        raw = await self._redis.get(self._key(content_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        return SkillContent(**json.loads(text))


def estimate_tokens(text: str) -> int:
    """无服务商 tokenizer 时的保守字符估算，仅用于观测，不作为计费口径。"""
    return max(1, (len(text) + 1) // 2) if text else 0


class SkillContentCollapseHook:
    """超过 N 轮的非活跃内容折叠为可恢复引用，活跃与未决调用始终保留。"""

    def __init__(self, store: SkillContentStore, *, collapse_after_turns: int = 3) -> None:
        if collapse_after_turns < 1:
            raise ValueError("collapse_after_turns 必须大于 0")
        self._store = store
        self._collapse_after = collapse_after_turns

    async def register(self, content: SkillContent) -> SkillContent:
        await self._store.save(content)
        return content

    async def collapse(self, contents: Sequence[SkillContent],
                       *, current_turn: int) -> CollapseResult:
        blocks: list[SkillContent | FoldedSkillContent] = []
        folded_tokens = 0
        for content in contents:
            age = current_turn - content.loaded_turn
            protected = (
                content.active
                or content.pending_tool_call
                or age < self._collapse_after
            )
            if protected:
                blocks.append(content)
                continue
            summary = content.summary or content.text[:80]
            blocks.append(FoldedSkillContent(
                content_id=content.content_id,
                skill_id=content.skill_id,
                loaded_turn=content.loaded_turn,
                summary=summary,
                reference=f"skill-content://{content.content_id}",
            ))
            folded_tokens += estimate_tokens(content.text)
        folded_count = sum(isinstance(block, FoldedSkillContent) for block in blocks)
        return CollapseResult(tuple(blocks), folded_count, folded_tokens)

    async def restore(self, reference: str) -> SkillContent:
        content_id = reference.removeprefix("skill-content://")
        content = await self._store.get(content_id)
        if content is None:
            raise ContentNotRecoverable(f"折叠内容引用已失效：{reference}")
        return content


@dataclass(frozen=True)
class PromptSections:
    """静态前缀与动态段分开；时间只能进入动态段。"""

    static_version: str
    static_prefix: str
    dynamic_section: str
    static_prefix_hash: str

    @property
    def system_prompt(self) -> str:
        return f"{self.static_prefix}\n\n[dynamic]\n{self.dynamic_section}"


def build_prompt_sections(
    *,
    static_version: str,
    instructions: str,
    skill_static_contents: Sequence[str] = (),
    dynamic: Mapping[str, str],
) -> PromptSections:
    static_prefix = "\n".join([
        f"prompt_version: {static_version}",
        "[instructions]",
        instructions,
        *skill_static_contents,
    ])
    dynamic_section = "\n".join(f"{key}: {dynamic[key]}" for key in sorted(dynamic))
    digest = hashlib.sha256(static_prefix.encode("utf-8")).hexdigest()
    return PromptSections(static_version, static_prefix, dynamic_section, digest)


@dataclass(frozen=True)
class PromptResolution:
    """一次 Prompt 组装结果：是否命中静态前缀，从而复用其 KV Cache。"""

    system_prompt: str
    prefix_reused: bool


class PromptPrefixCache:
    """按静态前缀哈希复用 KV Cache 前缀。

    静态前缀（版本 + 指令 + 静态 Skill 内容）不变时，其 KV Cache 可整段复用，
    只需重算尾部动态段（时间等）；静态前缀变化才失效重建。动态段从不进入前缀。
    """

    def __init__(self) -> None:
        self._last_hash: str | None = None
        self._hits = 0
        self._misses = 0

    def resolve(self, sections: PromptSections) -> PromptResolution:
        reused = self._last_hash == sections.static_prefix_hash
        if reused:
            self._hits += 1
        else:
            self._misses += 1
            self._last_hash = sections.static_prefix_hash
        return PromptResolution(system_prompt=sections.system_prompt, prefix_reused=reused)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def reuse_rate(self) -> float:
        total = self._hits + self._misses
        return round(self._hits / total, 6) if total else 0.0