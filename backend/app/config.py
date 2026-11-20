"""运行配置：全部来自环境变量，缺失关键项时在启动期失败而不是运行期降级。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """关键配置缺失或非法。"""


_REQUIRED = (
    "REDIS_URL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "EMBEDDING_API_KEY",
)


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是数字，实际为 {raw!r}") from exc


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数，实际为 {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    redis_url: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_timeout_seconds: float
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_timeout_seconds: float
    vector_min_score: float
    vector_margin: float
    typesafe_api_key: str
    jev_model: str | None
    jev_base_url: str | None
    jev_timeout_seconds: float
    jev_confidence_threshold: float
    key_prefix: str
    audit_ttl_seconds: int
    plan_ttl_seconds: int
    review_ttl_seconds: int
    continuation_ttl_seconds: int
    skill_state_ttl_seconds: int
    skill_content_ttl_seconds: int
    skill_base_dir: str
    skill_timeout_seconds: float
    max_review_rounds: int
    collapse_after_turns: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        if env is None:
            load_dotenv(Path(__file__).resolve().parent.parent / ".env")
            env = os.environ
        missing = [key for key in _REQUIRED if not env.get(key)]
        if missing:
            raise ConfigError(
                "缺少必需环境变量：" + "、".join(missing) + "（参考 .env.example）"
            )
        return cls(
            redis_url=env["REDIS_URL"],
            llm_base_url=env["LLM_BASE_URL"].rstrip("/"),
            llm_api_key=env["LLM_API_KEY"],
            llm_model=env["LLM_MODEL"],
            llm_temperature=_float(env, "LLM_TEMPERATURE", 0.0),
            llm_timeout_seconds=_float(env, "LLM_TIMEOUT_SECONDS", 8.0),
            embedding_base_url=(
                env.get("EMBEDDING_BASE_URL") or "https://api.siliconflow.cn/v1"
            ).rstrip("/"),
            embedding_api_key=env["EMBEDDING_API_KEY"],
            embedding_model=env.get("EMBEDDING_MODEL") or "BAAI/bge-m3",
            embedding_timeout_seconds=_float(env, "EMBEDDING_TIMEOUT_SECONDS", 5.0),
            vector_min_score=_float(env, "VECTOR_MIN_SCORE", 0.62),
            vector_margin=_float(env, "VECTOR_MARGIN", 0.05),
            typesafe_api_key=env.get("TYPESAFE_API_KEY", ""),
            jev_model=env.get("JEV_MODEL") or None,
            jev_base_url=(env.get("TYPESAFE_BASE_URL") or "").rstrip("/") or None,
            jev_timeout_seconds=_float(env, "JEV_TIMEOUT_SECONDS", 18.0),
            jev_confidence_threshold=_float(env, "JEV_CONFIDENCE_THRESHOLD", 0.75),
            key_prefix=env.get("KEY_PREFIX") or "ta:route",
            audit_ttl_seconds=_int(env, "AUDIT_TTL_SECONDS", 604800),
            plan_ttl_seconds=_int(env, "PLAN_TTL_SECONDS", 604800),
            review_ttl_seconds=_int(env, "REVIEW_TTL_SECONDS", 604800),
            continuation_ttl_seconds=_int(env, "CONTINUATION_TTL_SECONDS", 3600),
            skill_state_ttl_seconds=_int(env, "SKILL_STATE_TTL_SECONDS", 86400),
            skill_content_ttl_seconds=_int(env, "SKILL_CONTENT_TTL_SECONDS", 604800),
            skill_base_dir=env.get("SKILL_BASE_DIR") or ".runtime/skill_executions",
            skill_timeout_seconds=_float(env, "SKILL_TIMEOUT_SECONDS", 30.0),
            max_review_rounds=_int(env, "MAX_REVIEW_ROUNDS", 3),
            collapse_after_turns=_int(env, "COLLAPSE_AFTER_TURNS", 3),
        )