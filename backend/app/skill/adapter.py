"""AgentScope Hook 风格的 Skill 适配层。

每次执行都重建逻辑上下文：按用户注入凭证、创建独立目录、限定 Redis 状态
命名空间；不读取节点本地登录态，也不在日志或结果中输出秘密值。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class SkillStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_AUTHORIZATION = "needs_authorization"


class SkillErrorType(str, Enum):
    AUTHORIZATION = "authorization"
    TIMEOUT = "timeout"
    INPUT = "input"
    THIRD_PARTY = "third_party"
    STATE_MISSING = "state_missing"
    RESULT_PARSE = "result_parse"
    ENVIRONMENT = "environment"


class SkillStateError(RuntimeError):
    """状态命名空间缺失或与执行上下文不一致。"""


@dataclass(frozen=True)
class SkillExecutionContext:
    user_id: str
    session_id: str
    execution_id: str
    skill_name: str
    workdir: Path
    state_namespace: str
    timeout_seconds: float


@dataclass(frozen=True)
class SkillRequest:
    user_id: str
    session_id: str
    skill_name: str
    command: Sequence[str]
    payload: Mapping[str, Any] | None = None
    execution_id: str | None = None
    timeout_seconds: float | None = None
    credential_env: str = "SKILL_API_KEY"


@dataclass(frozen=True)
class SkillResult:
    execution_id: str
    status: SkillStatus
    output: dict[str, Any] | None = None
    error_type: SkillErrorType | None = None
    error_message: str | None = None
    duration_ms: float = 0.0


class CredentialResolver(Protocol):
    async def resolve(self, *, user_id: str, skill_name: str) -> str | None:
        """按用户解析 API Key；禁止回退到他人或节点默认凭证。"""


class InMemoryCredentialResolver:
    def __init__(self, credentials: Mapping[tuple[str, str], str] | None = None) -> None:
        self._credentials = dict(credentials or {})

    def set(self, user_id: str, skill_name: str, api_key: str) -> None:
        self._credentials[(user_id, skill_name)] = api_key

    async def resolve(self, *, user_id: str, skill_name: str) -> str | None:
        return self._credentials.get((user_id, skill_name))


class RedisCredentialResolver:
    """凭证只从受控存储读取，不写入方案、日志或 Skill 状态。"""

    def __init__(self, redis, prefix: str) -> None:
        self._redis = redis
        self._prefix = prefix

    async def resolve(self, *, user_id: str, skill_name: str) -> str | None:
        raw = await self._redis.get(f"{self._prefix}:credential:{user_id}:{skill_name}")
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)


def build_state_namespace(user_id: str, session_id: str, skill_name: str,
                          execution_id: str) -> str:
    return (
        f"user:{user_id}:session:{session_id}:skill:{skill_name}:exec:{execution_id}"
    )


def validate_context(context: SkillExecutionContext) -> None:
    expected = build_state_namespace(
        context.user_id, context.session_id, context.skill_name, context.execution_id
    )
    if not context.state_namespace or context.state_namespace != expected:
        raise SkillStateError("Skill 状态命名空间缺失或与执行上下文不一致")


class SkillStateStore(Protocol):
    async def set(self, context: SkillExecutionContext, key: str,
                  value: Mapping[str, Any]) -> None:
        """在本次执行的状态命名空间写入 JSON 状态。"""

    async def get(self, context: SkillExecutionContext, key: str) -> dict[str, Any] | None:
        """只读取本次执行命名空间内的状态。"""


class InMemorySkillStateStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(self, context: SkillExecutionContext, key: str,
                  value: Mapping[str, Any]) -> None:
        validate_context(context)
        self._data[f"{context.state_namespace}:{key}"] = json.dumps(
            dict(value), ensure_ascii=False
        )

    async def get(self, context: SkillExecutionContext, key: str) -> dict[str, Any] | None:
        validate_context(context)
        raw = self._data.get(f"{context.state_namespace}:{key}")
        return json.loads(raw) if raw is not None else None


class RedisSkillStateStore:
    def __init__(self, redis, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, context: SkillExecutionContext, key: str) -> str:
        validate_context(context)
        return f"{self._prefix}:skill_state:{context.state_namespace}:{key}"

    async def set(self, context: SkillExecutionContext, key: str,
                  value: Mapping[str, Any]) -> None:
        await self._redis.set(
            self._key(context, key), json.dumps(dict(value), ensure_ascii=False),
            ex=self._ttl,
        )

    async def get(self, context: SkillExecutionContext, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(context, key))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else raw
        return json.loads(text)


_SAFE_PART = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_part(value: str) -> str:
    cleaned = _SAFE_PART.sub("_", value)
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("用户或执行标识不能用于独立目录")
    return cleaned


def _sanitize(text: str, secrets: Sequence[str]) -> str:
    sanitized = text
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "***")
    return sanitized[-500:]


class SkillAdapter:
    """统一执行入口，对应 Hook 的调用前、执行中、调用后三个阶段。"""

    def __init__(
        self,
        credentials: CredentialResolver,
        state_store: SkillStateStore,
        *,
        base_dir: str | Path,
        default_timeout_seconds: float = 30.0,
        cleanup_workdir: bool = True,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._credentials = credentials
        self._state = state_store
        self._base_dir = Path(base_dir)
        self._default_timeout = default_timeout_seconds
        self._cleanup = cleanup_workdir
        self._environ = environ

    async def execute(self, request: SkillRequest) -> SkillResult:
        started = time.perf_counter()
        execution_id = request.execution_id or uuid.uuid4().hex

        def result(status: SkillStatus, **kwargs: Any) -> SkillResult:
            return SkillResult(
                execution_id=execution_id,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                **kwargs,
            )

        try:
            command = self._validate_request(request)
        except ValueError as exc:
            return result(SkillStatus.FAILED, error_type=SkillErrorType.INPUT,
                          error_message=str(exc))

        credential = await self._credentials.resolve(
            user_id=request.user_id, skill_name=request.skill_name
        )
        if not credential:
            return result(
                SkillStatus.NEEDS_AUTHORIZATION,
                error_type=SkillErrorType.AUTHORIZATION,
                error_message=f"用户 {request.user_id} 缺少 Skill {request.skill_name} 的授权凭证",
            )

        timeout = request.timeout_seconds or self._default_timeout
        try:
            workdir = self._base_dir / _safe_part(request.user_id) / _safe_part(execution_id)
            workdir.mkdir(parents=True, exist_ok=False)
        except ValueError as exc:
            return result(SkillStatus.FAILED, error_type=SkillErrorType.INPUT,
                          error_message=str(exc))
        except OSError as exc:
            return result(SkillStatus.FAILED, error_type=SkillErrorType.ENVIRONMENT,
                          error_message=f"独立执行目录创建失败：{exc}")

        namespace = build_state_namespace(
            request.user_id, request.session_id, request.skill_name, execution_id
        )
        context = SkillExecutionContext(
            user_id=request.user_id,
            session_id=request.session_id,
            execution_id=execution_id,
            skill_name=request.skill_name,
            workdir=workdir,
            state_namespace=namespace,
            timeout_seconds=timeout,
        )
        validate_context(context)
        env = dict(self._environ or os.environ)
        env.update({
            request.credential_env: credential,
            "SKILL_USER_ID": request.user_id,
            "SKILL_SESSION_ID": request.session_id,
            "SKILL_EXECUTION_ID": execution_id,
            "SKILL_STATE_NAMESPACE": namespace,
            "PYTHONIOENCODING": "utf-8",
        })
        input_bytes = (
            json.dumps(dict(request.payload), ensure_ascii=False).encode("utf-8")
            if request.payload is not None else None
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workdir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=input_bytes), timeout=timeout
                )
            except TimeoutError:
                process.kill()
                await process.communicate()
                return result(SkillStatus.FAILED, error_type=SkillErrorType.TIMEOUT,
                              error_message=f"Skill 执行超过 {timeout} 秒")
        except OSError as exc:
            return result(SkillStatus.FAILED, error_type=SkillErrorType.ENVIRONMENT,
                          error_message=_sanitize(str(exc), [credential]))
        finally:
            if self._cleanup:
                shutil.rmtree(workdir, ignore_errors=True)

        stderr_text = _sanitize(stderr.decode("utf-8", errors="replace"), [credential])
        if process.returncode != 0:
            return result(
                SkillStatus.FAILED,
                error_type=SkillErrorType.THIRD_PARTY,
                error_message=f"第三方进程退出码 {process.returncode}：{stderr_text}",
            )
        try:
            output = json.loads(stdout.decode("utf-8").strip())
            if not isinstance(output, dict):
                raise ValueError("顶层不是 JSON 对象")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return result(SkillStatus.FAILED, error_type=SkillErrorType.RESULT_PARSE,
                          error_message=f"第三方输出无法解析：{exc}")

        if output.get("status") == "needs_authorization":
            return result(
                SkillStatus.NEEDS_AUTHORIZATION,
                error_type=SkillErrorType.AUTHORIZATION,
                error_message=str(output.get("message") or "第三方要求重新授权"),
                output=output,
            )
        return result(SkillStatus.SUCCESS, output=output)

    @staticmethod
    def validate_request(request: SkillRequest) -> tuple[str, ...]:
        """校验请求并返回规范化命令，供执行入口与 Hook 共用。"""
        return SkillAdapter._validate_request(request)

    @staticmethod
    def _validate_request(request: SkillRequest) -> tuple[str, ...]:
        if not request.user_id or not request.session_id or not request.skill_name:
            raise ValueError("用户、会话与 Skill 标识不能为空")
        if not request.command or any(not isinstance(part, str) or not part
                                      for part in request.command):
            raise ValueError("执行命令不能为空")
        timeout = request.timeout_seconds
        if timeout is not None and timeout <= 0:
            raise ValueError("超时时间必须大于 0")
        return tuple(request.command)