"""AgentScope 1.x 原生 Hook 与第三方 Skill 工具接线。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock
from agentscope.model import ChatModelBase, OpenAIChatModel
from agentscope.tool import Toolkit, ToolResponse

from app.config import Settings
from app.context.agentscope_memory import SkillContentMemory
from app.context.folding import (
    InMemorySkillContentStore,
    SkillContentCollapseHook,
)
from app.skill.adapter import SkillAdapter, SkillRequest, SkillResult

SKILL_TOOL_NAME = "execute_travel_skill"
RESTORE_TOOL_NAME = "restore_travel_skill_output"
SKILL_HOOK_NAME = "travel_skill_request_validation"


def _request_from_input(arguments: Mapping[str, Any]) -> SkillRequest:
    return SkillRequest(
        user_id=arguments.get("user_id") or "",
        session_id=arguments.get("session_id") or "",
        skill_name=arguments.get("skill_name") or "",
        command=arguments.get("command") or (),
        payload=arguments.get("payload"),
        execution_id=arguments.get("execution_id"),
        timeout_seconds=arguments.get("timeout_seconds"),
        credential_env=arguments.get("credential_env") or "SKILL_API_KEY",
    )


def _result_payload(result: SkillResult) -> dict[str, Any]:
    return {
        "execution_id": result.execution_id,
        "status": result.status.value,
        "output": result.output,
        "error_type": result.error_type.value if result.error_type else None,
        "error_message": result.error_message,
        "duration_ms": result.duration_ms,
    }


class AgentScopeSkillHook:
    """在 AgentScope 工具执行前复用 Skill 适配器的请求校验。"""

    def __init__(self, adapter: SkillAdapter) -> None:
        self._adapter = adapter

    def pre_acting(self, agent: ReActAgent, kwargs: dict[str, Any]) -> None:
        tool_call = kwargs["tool_call"]
        if tool_call["name"] != SKILL_TOOL_NAME:
            return None
        self._adapter.validate_request(_request_from_input(tool_call.get("input") or {}))
        return None

    def register(self, agent: ReActAgent) -> None:
        agent.register_instance_hook(
            hook_type="pre_acting",
            hook_name=SKILL_HOOK_NAME,
            hook=self.pre_acting,
        )


def _build_toolkit(
    adapter: SkillAdapter,
    content_hook: SkillContentCollapseHook,
) -> Toolkit:
    toolkit = Toolkit()

    async def execute_travel_skill(
        user_id: str,
        session_id: str,
        skill_name: str,
        command: list[str],
        payload: dict[str, Any] | None = None,
        execution_id: str | None = None,
        timeout_seconds: float | None = None,
        credential_env: str = "SKILL_API_KEY",
    ) -> ToolResponse:
        """在隔离目录中执行已授权的差旅第三方 CLI。"""
        request = SkillRequest(
            user_id=user_id,
            session_id=session_id,
            skill_name=skill_name,
            command=command,
            payload=payload,
            execution_id=execution_id,
            timeout_seconds=timeout_seconds,
            credential_env=credential_env,
        )
        result = await adapter.execute(request)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=json.dumps(_result_payload(result), ensure_ascii=False),
                ),
            ],
        )

    toolkit.register_tool_function(
        execute_travel_skill,
        func_name=SKILL_TOOL_NAME,
        func_description=(
            "执行差旅第三方 Skill。必须使用内部编排提供的用户、会话和命令参数；"
            "凭证由服务端按用户注入，不得传入或输出 API Key。"
        ),
    )

    async def restore_travel_skill_output(reference: str) -> ToolResponse:
        """按折叠引用恢复较早的 Skill 原始输出。"""
        content = await content_hook.restore(reference)
        return ToolResponse(content=[TextBlock(type="text", text=content.text)])

    toolkit.register_tool_function(
        restore_travel_skill_output,
        func_name=RESTORE_TOOL_NAME,
        func_description=(
            "仅当用户需要较早 Skill 结果的具体细节时，使用对话中的 "
            "skill-content:// 引用恢复原始输出。引用失效时如实告知，不得猜测内容。"
        ),
    )
    return toolkit


def build_skill_agent(
    settings: Settings,
    adapter: SkillAdapter,
    model: ChatModelBase | None = None,
    content_hook: SkillContentCollapseHook | None = None,
) -> ReActAgent:
    """创建真实 ReActAgent，并注册 Skill 工具和实例级 pre_acting Hook。"""
    content_store = content_hook or SkillContentCollapseHook(
        InMemorySkillContentStore(),
        collapse_after_turns=settings.collapse_after_turns,
    )
    chat_model = model or OpenAIChatModel(
        model_name=settings.llm_model,
        api_key=settings.llm_api_key,
        stream=False,
        client_kwargs={
            "base_url": settings.llm_base_url,
            "timeout": settings.llm_timeout_seconds,
        },
        generate_kwargs={
            "temperature": settings.llm_temperature,
            "extra_body": {"enable_thinking": False},
        },
    )
    agent = ReActAgent(
        name="travel_skill_agent",
        sys_prompt=(
            "你是差旅第三方 Skill 执行 Agent。仅执行内部编排明确提供的工具请求，"
            "不得编造用户身份、凭证或命令，也不得泄露任何秘密。"
            "较早 Skill 输出可能以摘要和 skill-content:// 引用呈现；用户询问具体细节时，"
            "先调用恢复工具读取原文，引用失效则明确说明。"
        ),
        model=chat_model,
        formatter=OpenAIChatFormatter(),
        toolkit=_build_toolkit(adapter, content_store),
        memory=SkillContentMemory(
            content_store,
            tool_name=SKILL_TOOL_NAME,
        ),
    )
    AgentScopeSkillHook(adapter).register(agent)
    return agent