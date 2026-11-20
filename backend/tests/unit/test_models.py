import pytest

from app.config import ConfigError, Settings
from app.models import (
    Candidate,
    DecisionLayer,
    RouteDecision,
    RouteType,
    clarify,
)


def _valid_env() -> dict[str, str]:
    return {
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "LLM_BASE_URL": "https://api.example.com/v1/",
        "LLM_API_KEY": "sk-test",
        "LLM_MODEL": "test-model",
        "EMBEDDING_API_KEY": "sk-test",
    }


def test_settings_loaded_from_env():
    settings = Settings.from_env(_valid_env())
    assert settings.llm_base_url == "https://api.example.com/v1"
    assert settings.embedding_base_url == "https://api.siliconflow.cn/v1"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.vector_min_score == 0.62
    assert settings.typesafe_api_key == ""
    assert settings.jev_model is None
    assert settings.jev_base_url is None
    assert settings.jev_timeout_seconds == 18.0
    assert settings.jev_confidence_threshold == 0.75
    assert settings.key_prefix == "ta:route"


def test_settings_override_and_type_check():
    env = _valid_env() | {
        "VECTOR_MARGIN": "0.12",
        "AUDIT_TTL_SECONDS": "60",
        "EMBEDDING_BASE_URL": "https://embedding.example/v1/",
        "EMBEDDING_MODEL": "custom-embedding",
        "TYPESAFE_API_KEY": "tsk-test",
        "JEV_MODEL": "system-one-pro",
        "TYPESAFE_BASE_URL": "https://jev.example.com/v1/",
        "JEV_TIMEOUT_SECONDS": "20",
    }
    settings = Settings.from_env(env)
    assert settings.vector_margin == 0.12
    assert settings.audit_ttl_seconds == 60
    assert settings.embedding_base_url == "https://embedding.example/v1"
    assert settings.embedding_model == "custom-embedding"
    assert settings.typesafe_api_key == "tsk-test"
    assert settings.jev_model == "system-one-pro"
    assert settings.jev_base_url == "https://jev.example.com/v1"
    assert settings.jev_timeout_seconds == 20

    with pytest.raises(ConfigError):
        Settings.from_env(_valid_env() | {"VECTOR_MARGIN": "high"})


def test_settings_reports_all_missing_keys():
    env = {"REDIS_URL": "redis://x"}
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    message = str(exc.value)
    assert "LLM_API_KEY" in message
    assert "EMBEDDING_API_KEY" in message
    assert "REDIS_URL" not in message


def test_routed_decision_requires_type_and_confidence():
    with pytest.raises(ValueError):
        RouteDecision(trace_id="t1", decision_layer=DecisionLayer.L1_RULE)

    with pytest.raises(ValueError):
        RouteDecision(
            trace_id="t1",
            route_type=RouteType.KNOWLEDGE_QA,
            confidence=0.0,
            decision_layer=DecisionLayer.L1_RULE,
        )


def test_clarify_decision_has_no_route_type():
    decision = clarify("t2", "两意图冲突", "请确认是要改行程还是查政策？")
    assert decision.decision_layer is DecisionLayer.CLARIFY
    assert decision.route_type is None
    assert decision.target_agent == "clarification"
    assert decision.fallback is True

    with pytest.raises(ValueError):
        RouteDecision(
            trace_id="t3",
            route_type=RouteType.WORKFLOW,
            confidence=0.9,
            decision_layer=DecisionLayer.CLARIFY,
            clarification="?",
        )


def test_target_agent_mapping_and_candidates():
    decision = RouteDecision(
        trace_id="t4",
        route_type=RouteType.TASK_COORDINATION,
        confidence=0.91,
        decision_layer=DecisionLayer.JEV,
        candidates=[
            Candidate(route_type=RouteType.TASK_COORDINATION, score=0.91),
            Candidate(route_type=RouteType.KNOWLEDGE_QA, score=0.06),
        ],
    )
    assert decision.target_agent == "task_coordination_agent"
    assert decision.candidates[0].score == 0.91