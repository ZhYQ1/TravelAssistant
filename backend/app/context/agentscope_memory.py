"""AgentScope memory integration for recoverable Skill output folding."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from agentscope.memory import InMemoryMemory
from agentscope.message import Msg

from app.context.folding import (
    FoldedSkillContent,
    SkillContent,
    SkillContentCollapseHook,
)


class SkillContentMemory(InMemoryMemory):
    """Folds old Skill tool results when AgentScope builds the next model prompt."""

    def __init__(
        self,
        collapse_hook: SkillContentCollapseHook,
        *,
        tool_name: str,
    ) -> None:
        super().__init__()
        self._collapse_hook = collapse_hook
        self._tool_name = tool_name
        self._loaded_turns: dict[str, int] = {}

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        messages = await super().get_memory(
            mark=mark,
            exclude_mark=exclude_mark,
            prepend_summary=prepend_summary,
            **kwargs,
        )
        current_turn = sum(message.role == "user" for message in messages)
        candidates: dict[str, SkillContent] = {}
        tool_results: dict[str, tuple[int, int]] = {}

        for message_index, message in enumerate(messages):
            for block_index, block in enumerate(message.get_content_blocks("tool_result")):
                if block.get("name") != self._tool_name:
                    continue
                output_text = _tool_output_text(block.get("output"))
                if not output_text or "skill-content://" in output_text:
                    continue
                content_id = hashlib.sha256(
                    f"{message.id}:{block.get('id', '')}".encode("utf-8")
                ).hexdigest()[:32]
                loaded_turn = self._loaded_turns.setdefault(content_id, current_turn)
                candidates[content_id] = SkillContent(
                    content_id=content_id,
                    skill_id=self._tool_name,
                    loaded_turn=loaded_turn,
                    text=output_text,
                    summary=output_text[:240],
                )
                tool_results[content_id] = (message_index, block_index)

        if not candidates:
            return messages

        for content in candidates.values():
            await self._collapse_hook.register(content)
        result = await self._collapse_hook.collapse(
            tuple(candidates.values()),
            current_turn=current_turn,
        )
        if not result.folded_ids:
            return messages

        folded_by_id = {
            block.content_id: block
            for block in result.blocks
            if isinstance(block, FoldedSkillContent)
        }
        transformed = list(messages)
        for content_id, folded in folded_by_id.items():
            message_index, block_index = tool_results[content_id]
            message = copy.deepcopy(transformed[message_index])
            blocks = message.get_content_blocks("tool_result")
            blocks[block_index]["output"] = [{
                "type": "text",
                "text": (
                    "[较早的 Skill 输出已折叠]\n"
                    f"摘要：{folded.summary}\n"
                    f"详情引用：{folded.reference}"
                ),
            }]
            transformed[message_index] = message
        return transformed


def _tool_output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if not isinstance(output, list):
        return ""
    text_blocks = [
        item.get("text", "")
        for item in output
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    return "\n".join(text_blocks)
