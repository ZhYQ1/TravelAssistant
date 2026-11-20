import asyncio

import pytest

from app.context.folding import (
    ContentNotRecoverable,
    FoldedSkillContent,
    InMemorySkillContentStore,
    PromptPrefixCache,
    PromptSections,
    SkillContent,
    SkillContentCollapseHook,
    build_prompt_sections,
)


def run(coro):
    return asyncio.run(coro)


def content(content_id: str, turn: int, **overrides) -> SkillContent:
    params = dict(
        content_id=content_id, skill_id="weather", loaded_turn=turn,
        text=f"{content_id} 的完整第三方 Skill 输出" * 20,
        summary=f"{content_id} 摘要",
    )
    params.update(overrides)
    return SkillContent(**params)


def test_old_inactive_content_folds_but_active_recent_and_pending_stay():
    store = InMemorySkillContentStore()
    hook = SkillContentCollapseHook(store, collapse_after_turns=3)
    contents = [
        content("old", 1),
        content("active", 1, active=True),
        content("pending", 1, pending_tool_call=True),
        content("recent", 4),
        content("current", 5),
    ]
    for item in contents:
        run(hook.register(item))

    result = run(hook.collapse(contents, current_turn=5))
    assert result.folded_ids == ("old",)
    assert result.folded_count == 1
    assert result.folded_tokens > 0
    assert [type(block) for block in result.blocks] == [
        FoldedSkillContent, SkillContent, SkillContent, SkillContent, SkillContent
    ]
    folded = result.blocks[0]
    assert folded.reference == "skill-content://old"
    assert folded.summary == "old 摘要"
    assert "完整第三方" not in folded.summary


def test_folded_reference_restores_original_and_missing_reference_fails():
    store = InMemorySkillContentStore()
    hook = SkillContentCollapseHook(store, collapse_after_turns=1)
    original = content("old", 1)
    run(hook.register(original))
    result = run(hook.collapse([original], current_turn=3))

    restored = run(hook.restore(result.blocks[0].reference))
    assert restored == original

    with pytest.raises(ContentNotRecoverable):
        run(hook.restore("skill-content://expired"))


def test_current_turn_is_never_folded_even_with_threshold_one():
    hook = SkillContentCollapseHook(InMemorySkillContentStore(), collapse_after_turns=1)
    current = content("current", 5)
    result = run(hook.collapse([current], current_turn=5))
    assert result.folded_count == 0
    assert result.blocks == (current,)


def _sections(now: str, version: str = "prompt-v1") -> PromptSections:
    return build_prompt_sections(
        static_version=version,
        instructions="你是差旅助手，不得编造候选方案。",
        skill_static_contents=["[skill:weather]\n查询天气时必须给出城市。"],
        dynamic={"current_time": now, "session_id": "s-1"},
    )


def test_time_changes_only_dynamic_section_and_preserves_static_hash():
    first = _sections("2026-11-19T10:00:00+08:00")
    second = _sections("2026-11-19T10:01:00+08:00")

    assert first.static_prefix == second.static_prefix
    assert first.static_prefix_hash == second.static_prefix_hash
    assert first.dynamic_section != second.dynamic_section
    assert first.system_prompt.startswith(first.static_prefix)
    assert "current_time" not in first.static_prefix


def test_static_version_change_invalidates_old_cache_prefix():
    first = _sections("2026-11-19T10:00:00+08:00", version="prompt-v1")
    second = _sections("2026-11-19T10:00:00+08:00", version="prompt-v2")
    assert first.static_prefix != second.static_prefix
    assert first.static_prefix_hash != second.static_prefix_hash
    assert first.dynamic_section == second.dynamic_section


def test_prefix_cache_reuses_static_prefix_across_time_changes():
    cache = PromptPrefixCache()
    first = cache.resolve(_sections("2026-11-19T10:00:00+08:00"))
    second = cache.resolve(_sections("2026-11-19T10:01:00+08:00"))

    assert first.prefix_reused is False
    assert second.prefix_reused is True  # 静态前缀不变 => KV Cache 前缀可复用
    assert second.system_prompt != first.system_prompt  # 动态段（时间）仍更新
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.reuse_rate == 0.5


def test_prefix_cache_invalidates_on_static_version_change():
    cache = PromptPrefixCache()
    cache.resolve(_sections("2026-11-19T10:00:00+08:00", version="prompt-v1"))
    third = cache.resolve(_sections("2026-11-19T10:00:00+08:00", version="prompt-v2"))

    assert third.prefix_reused is False
    assert cache.misses == 2
    assert cache.hits == 0
    assert cache.reuse_rate == 0.0