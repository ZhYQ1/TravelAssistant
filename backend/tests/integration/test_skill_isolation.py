"""两个用户并发执行同一 CLI 的隔离测试（真实子进程）。"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from app.skill.adapter import (
    InMemoryCredentialResolver,
    InMemorySkillStateStore,
    SkillAdapter,
    SkillRequest,
    SkillStatus,
)

pytestmark = pytest.mark.integration


def test_concurrent_users_have_distinct_credentials_directories_and_namespaces(tmp_path):
    async def scenario():
        credentials = InMemoryCredentialResolver({
            ("u-1", "weather"): "key-user-one",
            ("u-2", "weather"): "key-user-two-longer",
        })
        adapter = SkillAdapter(
            credentials, InMemorySkillStateStore(), base_dir=tmp_path,
            cleanup_workdir=False,
        )
        script = (
            "import json, os, pathlib; "
            "pathlib.Path('marker.txt').write_text(os.environ['SKILL_USER_ID']); "
            "print(json.dumps({'user': os.environ['SKILL_USER_ID'], "
            "'key_length': len(os.environ['SKILL_API_KEY']), "
            "'namespace': os.environ['SKILL_STATE_NAMESPACE'], "
            "'cwd': os.getcwd(), "
            "'marker': pathlib.Path('marker.txt').read_text()}))"
        )

        def make(user_id: str):
            return SkillRequest(
                user_id=user_id, session_id="shared-session", skill_name="weather",
                command=[sys.executable, "-c", script],
            )

        return await asyncio.gather(adapter.execute(make("u-1")), adapter.execute(make("u-2")))

    first, second = asyncio.run(scenario())
    assert first.status is second.status is SkillStatus.SUCCESS
    assert first.output["user"] == first.output["marker"] == "u-1"
    assert second.output["user"] == second.output["marker"] == "u-2"
    assert first.output["key_length"] == len("key-user-one")
    assert second.output["key_length"] == len("key-user-two-longer")
    assert first.output["cwd"] != second.output["cwd"]
    assert first.output["namespace"] != second.output["namespace"]
    assert "key-user-one" not in json.dumps(first.output)
    assert "key-user-two-longer" not in json.dumps(second.output)
