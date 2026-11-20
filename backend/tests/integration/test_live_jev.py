"""Live Jev integration test; requires a TypeSafe API key (official Python SDK)."""

from __future__ import annotations

import asyncio
import os

import pytest

from app.models import RouteType
from app.routing.jev import JevAdapterClient, JevRouter

pytestmark = pytest.mark.integration

if not (os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")):
    pytest.skip("缺少 TYPESAFE_API_KEY/JEV_API_KEY", allow_module_level=True)


def test_official_jev_choice_routes_travel_question():
    client = JevAdapterClient(
        api_key=os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY"),
        model=os.environ.get("JEV_MODEL") or None,
        base_url=os.environ.get("TYPESAFE_BASE_URL") or None,
        timeout_seconds=float(os.environ.get("JEV_TIMEOUT_SECONDS", "18")),
    )
    router = JevRouter(client, confidence_threshold=0.75)

    result = asyncio.run(router.decide_route("发票丢了怎么办"))

    assert result.status == "ok", result.reason
    assert result.value is RouteType.KNOWLEDGE_QA
    assert 0.0 <= result.confidence <= 1.0