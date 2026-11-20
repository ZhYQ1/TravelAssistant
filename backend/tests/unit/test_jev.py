import asyncio
from types import SimpleNamespace

import pytest
from typesafe_sdk import Choice, ChoiceAnswer, NoulAnswer, ScoreAnswer, TypeSafeError

from app.llm.client import LLMError, parse_json_object
from app.models import RouteType
from app.routing.jev import JevAdapterClient, JevAdapterError, JevRouter


def run(coro):
    return asyncio.run(coro)


class FakeJevClient:
    def __init__(self, response=None, error=None):
        self.response = response or {}
        self.error = error
        self.calls = []

    async def decide_route(self, text, options):
        self.calls.append((text, options))
        if self.error:
            raise self.error
        return self.response


def make_router(response, *, error=None, threshold=0.75):
    client = FakeJevClient(response, error=error)
    return JevRouter(client, confidence_threshold=threshold), client


def test_official_choice_result_is_validated_and_thresholded():
    router, client = make_router({
        "choice": "knowledge_qa",
        "confidence": 0.93,
        "probabilities": {
            "task_coordination": 0.03,
            "knowledge_qa": 0.91,
            "workflow": 0.06,
        },
    })
    result = run(router.decide_route("报销标准是多少"))

    assert result.status == "ok"
    assert result.value is RouteType.KNOWLEDGE_QA
    assert result.confidence == pytest.approx(0.93)
    assert [candidate.route_type for candidate in result.candidates] == [
        RouteType.KNOWLEDGE_QA,
        RouteType.WORKFLOW,
        RouteType.TASK_COORDINATION,
    ]
    assert result.candidates[0].score == pytest.approx(0.91)
    assert router.is_confident(result) is True
    assert client.calls[0][0] == "报销标准是多少"
    assert client.calls[0][1]["task_coordination"]


def test_choice_outside_supplied_options_is_rejected():
    router, _ = make_router({"choice": "workflow", "confidence": 0.9})
    result = run(router.decide_route(
        "查差旅政策",
        options=(RouteType.KNOWLEDGE_QA,),
    ))

    assert result.status == "invalid"
    assert "not in the supplied options" in result.reason


@pytest.mark.parametrize(
    "answer",
    [
        {"choice": "workflow"},
        {"choice": "workflow", "confidence": True},
        {"choice": "workflow", "confidence": 1.4},
    ],
)
def test_invalid_confidence_is_rejected(answer):
    router, _ = make_router(answer)
    assert run(router.decide_route("提交审批")).status == "invalid"


def test_low_confidence_result_is_left_for_chain_fallback():
    router, _ = make_router({
        "choice": "workflow",
        "confidence": 0.42,
        "probabilities": {
            "task_coordination": 0.1,
            "knowledge_qa": 0.1,
            "workflow": 0.8,
        },
    })
    result = run(router.decide_route("走流程"))

    assert result.status == "ok"
    assert router.is_confident(result) is False


def test_adapter_failure_is_an_error_not_a_guessed_choice():
    router, _ = make_router({}, error=JevAdapterError("adapter unavailable"))
    result = run(router.decide_route("改行程"))

    assert result.status == "error"
    assert result.value is None
    assert "adapter unavailable" in result.reason


def test_missing_probability_distribution_is_rejected():
    router, _ = make_router({"choice": "workflow", "confidence": 0.9})
    result = run(router.decide_route("提交审批"))

    assert result.status == "invalid"
    assert "probabilities must cover exactly" in result.reason


class FakeSDKClient:
    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.requests = []

    async def system_one(self, state, questions, **kwargs):
        self.requests.append((state, questions))
        if self.error:
            raise self.error
        return SimpleNamespace(answers=self.answers)


def make_sdk_client(answers=None, error=None):
    return FakeSDKClient(answers=answers, error=error)


def test_sdk_client_maps_choice_answer_to_router_format():
    sdk = make_sdk_client(answers={
        "route": ChoiceAnswer(
            choice="workflow",
            confidence=0.88,
            probabilities={"workflow": 0.88},
        ),
    })
    client = JevAdapterClient(client=sdk)
    result = run(client.decide_route(
        "提交出差审批",
        {"workflow": "Start a travel approval workflow."},
    ))

    assert result == {
        "choice": "workflow",
        "confidence": 0.88,
        "probabilities": {"workflow": 0.88},
    }
    state, questions = sdk.requests[0]
    assert state == "提交出差审批"
    question = questions["route"]
    assert isinstance(question, Choice)
    assert question.criteria == {"workflow": "Start a travel approval workflow."}


def test_sdk_client_maps_noul_answer_to_probability():
    sdk = make_sdk_client(answers={"continuation": NoulAnswer(noul=0.81)})
    client = JevAdapterClient(client=sdk)
    probability = run(client.decide_noul(
        state="用户说：继续",
        instructions="判断是否应续跑当前任务",
        true_meaning="当前活跃任务可以直接继续",
        false_meaning="需要回到主路由",
    ))

    assert probability == pytest.approx(0.81)
    state, questions = sdk.requests[0]
    assert state == "用户说：继续"
    assert questions["continuation"].criteria == {
        "true": "当前活跃任务可以直接继续",
        "false": "需要回到主路由",
    }


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
def test_noul_rejects_out_of_range_probability(value):
    sdk = make_sdk_client(answers={"continuation": NoulAnswer(noul=value)})
    client = JevAdapterClient(client=sdk)

    with pytest.raises(JevAdapterError, match="Noul probability"):
        run(client.decide_noul(
            state="当前消息",
            instructions="判断是否需要续跑",
            true_meaning="是",
            false_meaning="否",
        ))


def test_sdk_client_maps_score_answer_and_ordered_rubric():
    sdk = make_sdk_client(answers={
        "score": ScoreAnswer(
            score=1.7,
            confidence=0.9,
            legend={0: "低", 1: "中", 2: "高"},
            probabilities={0: 0.1, 1: 0.2, 2: 0.7},
        ),
    })
    client = JevAdapterClient(client=sdk)
    result = run(client.score(
        state="行程信息",
        instructions="按给定标准评分",
        criteria=[
            {"name": "low", "description": "低"},
            {"name": "medium", "description": "中"},
            {"name": "high", "description": "高"},
        ],
    ))

    assert result == {
        "score": 1.7,
        "confidence": 0.9,
        "legend": {"0": "低", "1": "中", "2": "高"},
        "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
    }
    _, questions = sdk.requests[0]
    assert questions["score"].criteria == ["低", "中", "高"]


def test_score_rejects_empty_rubric():
    client = JevAdapterClient(client=make_sdk_client())

    with pytest.raises(JevAdapterError, match="non-empty rubric"):
        run(client.score(state="方案", instructions="评分", criteria=[]))


def test_unexpected_answer_type_is_rejected():
    sdk = make_sdk_client(answers={"route": NoulAnswer(noul=0.5)})
    client = JevAdapterClient(client=sdk)

    with pytest.raises(JevAdapterError, match="unexpected answer type"):
        run(client.decide_route("x", {"workflow": "Approval"}))


def test_sdk_error_surfaces_as_adapter_error():
    sdk = make_sdk_client(error=TypeSafeError("provider failed"))
    client = JevAdapterClient(client=sdk)

    with pytest.raises(JevAdapterError, match="Jev request failed"):
        run(client.decide_route("x", {"workflow": "Approval"}))


def test_parse_json_object_handles_fences_and_noise():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('结果如下 {"a": {"b": 2}} 完毕') == {"a": {"b": 2}}
    with pytest.raises(LLMError):
        parse_json_object("没有 JSON")
    with pytest.raises(LLMError):
        parse_json_object("[1, 2]")