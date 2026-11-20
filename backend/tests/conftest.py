import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "route_regression_60.jsonl"


def load_regression_cases() -> list[dict[str, str]]:
    """读取 60 条固定回归集；这是按 Spec 构造的数据集，非线上真实样本。"""
    with FIXTURE.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]
