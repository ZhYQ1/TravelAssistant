"""OpenAI 兼容的 Chat 客户端：只负责获取 JSON 对象，不做业务校验。"""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from app.observability.metrics import TokenUsage, TokenUsageSink


class LLMError(RuntimeError):
    """模型调用失败或返回无法解析的内容。"""


class JsonLLM(Protocol):
    async def complete_json(self, system: str, user: str, *, name: str) -> dict[str, Any]:
        """Return a JSON object from a chat-completions provider."""


def parse_json_object(content: str) -> dict[str, Any]:
    """容忍代码围栏与前后噪声，但要求最终是 JSON 对象。"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("模型输出中没有 JSON 对象")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型输出 JSON 解析失败：{exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("模型输出的 JSON 顶层不是对象")
    return parsed


class HttpChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        temperature: float = 0.0,
        timeout_seconds: float = 8.0,
        usage_sink: TokenUsageSink | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._usage_sink = usage_sink

    async def complete_json(self, system: str, user: str, *, name: str) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "temperature": self._temperature,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"{name} 请求失败：{exc}") from exc

        self._record_usage(name, data)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{name} 响应结构异常") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"{name} 返回空内容")
        return parse_json_object(content)

    def _record_usage(self, name: str, data: Any) -> None:
        """保留 complete_json 返回契约，仅旁路记录服务商 Token 用量。"""
        if self._usage_sink is None or not isinstance(data, dict):
            return
        raw = data.get("usage")
        if not isinstance(raw, dict):
            return
        try:
            prompt = int(raw.get("prompt_tokens", 0))
            completion = int(raw.get("completion_tokens", 0))
            total = int(raw.get("total_tokens", prompt + completion))
            self._usage_sink.record_token_usage(TokenUsage(
                name=name,
                model=self._model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            ))
        except Exception:  # noqa: BLE001 - 计量失败不影响模型调用结果
            pass