"""AgentScope 原生 Hook 生命周期测试：直接调用真实 ReActAgent._acting。"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import Mock

import pytest
from agentscope.agent import ReActAgent
from agentscope.message import Msg, TextBlock, ToolResultBlock, ToolUseBlock
from agentscope.model import ChatModelBase
from agentscope.tool import ToolResponse

from app.config import Settings
from app.context.agentscope_memory import SkillContentMemory
from app.context.folding import InMemorySkillContentStore, SkillContentCollapseHook
from app.skill.adapter import (
    InMemoryCredentialResolver,
    InMemorySkillStateStore,
    SkillAdapter,
)
from app.skill.agentscope_hook import (
    SKILL_HOOK_NAME,
    SKILL_TOOL_NAME,
    RESTORE_TOOL_NAME,
    build_skill_agent,
)

SCRIPT = (
    "import json, os, pathlib; "
    "pathlib.Path('marker.txt').write_text(os.environ['SKILL_USER_ID']); "
    "print(json.dumps({'user': os.environ['SKILL_USER_ID'], "
    "'key_length': len(os.environ['SKILL_API_KEY']), "
    "'namespace': os.environ['SKILL_STATE_NAMESPACE'], "
    "'cwd': os.getcwd(), "
    "'marker': pathlib.Path('marker.txt').read_text()}))"
)


def make_settings() -> Settings:
    return Settings.from_env({
        "REDIS_URL": "redis://localhost:6379/0",
        "LLM_BASE_URL": "http://localhost/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
        "EMBEDDING_BASE_URL": "http://localhost/v1",
        "EMBEDDING_API_KEY": "test-key",
        "EMBEDDING_MODEL": "test-model",
    })


def make_agent(tmp_path, credentials=None):
    adapter = SkillAdapter(
        InMemoryCredentialResolver(credentials or {("u-1", "weather"): "secret-key"}),
        InMemorySkillStateStore(),
        base_dir=tmp_path,
        cleanup_workdir=False,
    )
    calls = []
    original = adapter.execute

    async def tracked(request):
        calls.append(request)
        return await original(request)

    adapter.execute = tracked
    agent = build_skill_agent(make_settings(), adapter, Mock(spec=ChatModelBase))
    return agent, adapter, calls


def skill_call(script: str = SCRIPT, **overrides) -> ToolUseBlock:
    arguments = {
        "user_id": "u-1",
        "session_id": "s-1",
        "skill_name": "weather",
        "command": [sys.executable, "-c", script],
    }
    arguments.update(overrides)
    return ToolUseBlock(
        type="tool_use", id="call-skill", name=SKILL_TOOL_NAME, input=arguments
    )


def last_tool_result(agent: ReActAgent) -> list:
    memory = asyncio.run(agent.memory.get_memory())
    blocks = memory[-1].get_content_blocks("tool_result")
    assert blocks, "工具结果消息缺失"
    return blocks[-1]["output"]


def test_hook_is_registered_on_real_react_agent(tmp_path):
    agent, _, _ = make_agent(tmp_path)

    assert isinstance(agent, ReActAgent)
    assert SKILL_HOOK_NAME in agent._instance_pre_acting_hooks
    assert SKILL_TOOL_NAME in agent.toolkit.tools
    assert RESTORE_TOOL_NAME in agent.toolkit.tools
    assert isinstance(agent.memory, SkillContentMemory)


def test_agentscope_memory_folds_old_skill_results_and_can_restore_them():
    store = InMemorySkillContentStore()
    collapse_hook = SkillContentCollapseHook(store, collapse_after_turns=2)
    memory = SkillContentMemory(collapse_hook, tool_name=SKILL_TOOL_NAME)

    async def exercise():
        await memory.add(Msg("user", "查询天气", "user"))
        await memory.add(Msg(
            "system",
            [ToolResultBlock(
                type="tool_result",
                id="call-weather",
                name=SKILL_TOOL_NAME,
                output=[{"type": "text", "text": "深圳天气：晴，完整天气详情"}],
            )],
            "system",
        ))
        first = await memory.get_memory()
        assert "完整天气详情" in first[-1].get_content_blocks("tool_result")[0]["output"][0]["text"]

        await memory.add(Msg("user", "还有其他信息吗？", "user"))
        await memory.add(Msg("assistant", "暂时没有。", "assistant"))
        await memory.add(Msg("user", "刚才的完整天气结果是什么？", "user"))
        collapsed = await memory.get_memory()
        folded_output = collapsed[1].get_content_blocks("tool_result")[0]["output"][0]["text"]
        assert "较早的 Skill 输出已折叠" in folded_output
        assert "深圳天气" in folded_output
        reference = folded_output.split("详情引用：", 1)[1]
        restored = await collapse_hook.restore(reference)
        assert restored.text == "深圳天气：晴，完整天气详情"

    asyncio.run(exercise())


def test_pre_acting_allows_valid_request_and_executes_once(tmp_path):
    agent, _, calls = make_agent(tmp_path)

    asyncio.run(agent._acting(skill_call()))

    assert len(calls) == 1
    output = last_tool_result(agent)
    payload = json.loads(output[0]["text"])
    assert payload["status"] == "success"
    assert payload["output"]["user"] == payload["output"]["marker"] == "u-1"
    assert payload["output"]["key_length"] == len("secret-key")
    assert payload["output"]["namespace"]
    assert payload["output"]["cwd"].startswith(str(tmp_path))
    assert "secret-key" not in json.dumps(payload)


def test_pre_acting_blocks_invalid_request_before_execution(tmp_path):
    agent, _, calls = make_agent(tmp_path)

    with pytest.raises(ValueError, match="不能为空"):
        asyncio.run(agent._acting(skill_call(user_id="")))

    with pytest.raises(ValueError, match="超时时间必须大于 0"):
        asyncio.run(agent._acting(skill_call(timeout_seconds=0)))

    assert calls == []
    assert not any(tmp_path.iterdir())


def test_non_target_tool_is_not_affected_by_hook(tmp_path):
    agent, _, calls = make_agent(tmp_path)

    async def ping(value: str) -> ToolResponse:
        return ToolResponse(content=[TextBlock(type="text", text=f"pong:{value}")])

    agent.toolkit.register_tool_function(
        ping, func_name="ping", func_description="返回 pong"
    )

    asyncio.run(agent._acting(ToolUseBlock(
        type="tool_use", id="call-ping", name="ping", input={"value": "x"}
    )))

    assert calls == []
    output = last_tool_result(agent)
    assert output[0]["text"] == "pong:x"
