"""Phase 3 token accounting. Offline: FakeLLM only.

Convention (documented in sparkplan/llm/phase3/client.py): absent provider
usage -> LLMUsage(present=False, 0, 0), contributes nothing, never invented.
"""

from __future__ import annotations

import json

from sparkplan.llm.phase3 import UNKNOWN_USAGE, FakeLLM, LLMUsage
from sparkplan.planner.phase3 import Planner, PlanStatus
from tests.planner.phase3.conftest import valid_spec


def test_missing_usage_is_zero_and_not_present(catalog):
    llm = FakeLLM(script=[valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.total_prompt_tokens == 0
    assert result.total_completion_tokens == 0
    assert result.attempts[0].usage_present is False


def test_explicit_zero_usage(catalog):
    llm = FakeLLM(script=[(valid_spec(), LLMUsage(0, 0, True))])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.total_prompt_tokens == 0
    assert result.total_completion_tokens == 0
    assert result.attempts[0].usage_present is True


def test_cumulative_across_repairs(catalog):
    bad = json.loads(json.dumps(valid_spec()))
    bad["parts"][1]["part_id"] = "missing_part"
    llm = FakeLLM(
        script=[
            (bad, LLMUsage(100, 40, True)),
            (valid_spec(), LLMUsage(150, 70, True)),
        ]
    )
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.llm_calls == 2
    assert result.total_prompt_tokens == 250
    assert result.total_completion_tokens == 110
    assert [a.prompt_tokens for a in result.attempts] == [100, 150]
    assert [a.completion_tokens for a in result.attempts] == [40, 70]


def test_mixed_present_and_absent_usage(catalog):
    bad = json.loads(json.dumps(valid_spec()))
    bad["parts"][1]["part_id"] = "missing_part"
    llm = FakeLLM(
        script=[
            (bad, None),  # provider gave no usage
            (valid_spec(), LLMUsage(200, 80, True)),
        ]
    )
    result = Planner(llm, catalog).plan("a desk plant monitor")
    # Only the real numbers are summed; the unknown attempt adds nothing.
    assert result.total_prompt_tokens == 200
    assert result.total_completion_tokens == 80


def test_default_usage_is_unknown():
    assert UNKNOWN_USAGE.present is False
    assert UNKNOWN_USAGE.prompt_tokens == 0
