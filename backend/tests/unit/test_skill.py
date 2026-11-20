import asyncio
import json
import sys
from pathlib import Path

from app.skill.adapter import (
    InMemoryCredentialResolver,
    InMemorySkillStateStore,
    SkillAdapter,
    SkillErrorType,
    SkillExecutionContext,
    SkillRequest,
    SkillStateError,
    SkillStatus,
    build_state_namespace,
)


def run(coro):
    return asyncio.run(coro)


def make_adapter(tmp_path: Path):
    credentials = InMemoryCredentialResolver({("u-1", "weather"): "u1-secret"})
    states = InMemorySkillStateStore()
    return SkillAdapter(credentials, states, base_dir=tmp_path), states


def request(script: str, **overrides):
    params = dict(
        user_id="u-1",
        session_id="s-1",
        skill_name="weather",
        command=[sys.executable, "-c", script],
        payload={"city": "上海"},
    )
    params.update(overrides)
    return SkillRequest(**params)


def context(user_id: str, execution_id: str, tmp_path: Path) -> SkillExecutionContext:
    return SkillExecutionContext(
        user_id=user_id,
        session_id="s-1",
        execution_id=execution_id,
        skill_name="weather",
        workdir=tmp_path,
        state_namespace=build_state_namespace(user_id, "s-1", "weather", execution_id),
        timeout_seconds=1.0,
    )


def test_success_injects_user_credential_and_cleans_private_workdir(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    script = (
        "import json, os, sys; payload = json.load(sys.stdin); "
        "print(json.dumps({'status': 'ok', 'user': os.environ['SKILL_USER_ID'], "
        "'namespace': os.environ['SKILL_STATE_NAMESPACE'], "
        "'credential_length': len(os.environ.get('SKILL_API_KEY', '')), "
        "'cwd': os.getcwd(), 'payload': payload}))"
    )
    result = run(adapter.execute(request(script)))

    assert result.status is SkillStatus.SUCCESS
    assert result.output["user"] == "u-1"
    assert result.output["credential_length"] == len("u1-secret")
    assert result.output["payload"] == {"city": "上海"}
    assert "u1-secret" not in json.dumps(result.output)
    assert result.output["cwd"].endswith(result.execution_id)
    assert not Path(result.output["cwd"]).exists()


def test_missing_credential_never_falls_back_to_another_user(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    result = run(adapter.execute(request("print('{}')", user_id="u-2")))
    assert result.status is SkillStatus.NEEDS_AUTHORIZATION
    assert result.error_type is SkillErrorType.AUTHORIZATION


def test_third_party_failure_is_normalized_and_secret_is_masked(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    script = (
        "import os, sys; sys.stderr.write('denied ' + os.environ['SKILL_API_KEY']); "
        "sys.exit(3)"
    )
    result = run(adapter.execute(request(script)))
    assert result.status is SkillStatus.FAILED
    assert result.error_type is SkillErrorType.THIRD_PARTY
    assert "u1-secret" not in (result.error_message or "")
    assert "***" in (result.error_message or "")


def test_invalid_output_and_timeout_have_distinct_errors(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    invalid = run(adapter.execute(request("print('not-json')")))
    assert invalid.error_type is SkillErrorType.RESULT_PARSE

    timeout = run(adapter.execute(request("import time; time.sleep(5)",
                                          timeout_seconds=0.1)))
    assert timeout.status is SkillStatus.FAILED
    assert timeout.error_type is SkillErrorType.TIMEOUT


def test_invalid_command_is_an_input_error(tmp_path):
    adapter, _ = make_adapter(tmp_path)
    result = run(adapter.execute(request("", command=[])))
    assert result.status is SkillStatus.FAILED
    assert result.error_type is SkillErrorType.INPUT


def test_state_is_isolated_by_user_and_execution(tmp_path):
    _, states = make_adapter(tmp_path)
    first = context("u-1", "e-1", tmp_path)
    second = context("u-2", "e-1", tmp_path)
    run(states.set(first, "progress", {"step": 1}))
    run(states.set(second, "progress", {"step": 9}))

    assert run(states.get(first, "progress")) == {"step": 1}
    assert run(states.get(second, "progress")) == {"step": 9}
    assert run(states.get(first, "missing")) is None

    import pytest

    invalid = SkillExecutionContext(
        user_id="u-1", session_id="s-1", execution_id="e-1", skill_name="weather",
        workdir=tmp_path, state_namespace="user:u-2:session:s-1:skill:weather:exec:e-1",
        timeout_seconds=1.0,
    )
    with pytest.raises(SkillStateError):
        run(states.get(invalid, "progress"))
