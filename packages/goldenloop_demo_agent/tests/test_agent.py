import asyncio
import csv
from pathlib import Path

import pytest

from goldenloop_demo_agent import REVISIONS, lookup_customer, run_case
from goldenloop_demo_agent.cli import main
from goldenloop_eval import Case, evaluate
from goldenloop_eval.cli import load_cases

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic"


def test_fixed_vs_buggy_and_plausible_answer():
    case = load_cases(FIXTURES / "cases.json")[0]
    fixed = asyncio.run(run_case(case))
    buggy = asyncio.run(run_case(case, revision="buggy"))
    assert set(REVISIONS) == {"buggy", "fixed"}
    assert evaluate(case, fixed).gate == "pass"
    result = evaluate(case, buggy)
    assert result.gate == "fail"
    assert result.checks[0].status == "pass"
    assert next(c for c in result.checks if c.id == "correct-customer-argument").status == "fail"
    assert fixed.tool_calls[0].arguments == {"customer_id": "C-123"}
    assert buggy.tool_calls[0].arguments == {"customer_id": "C-999"}
    assert fixed.tool_calls[0].result["synthetic"] is True
    assert fixed.mode == "mock" and fixed.trace_complete
    assert fixed.usage == {} and fixed.latency_ms is None


@pytest.mark.parametrize("revision,customer", [("buggy", "C-999"), ("fixed", "C-123")])
def test_generated_history_not_reference_answers(revision, customer):
    case = load_cases(FIXTURES / "cases.json")[0]
    for turn in case.turns:
        turn.reference_answer = "NEVER INSERT THIS REFERENCE"
    obs = asyncio.run(run_case(case, revision=revision))
    answers = [m["content"] for m in obs.messages if m["role"] == "assistant"]
    assert answers[0] in answers[1]
    assert customer in answers[1]
    assert "NEVER INSERT" not in obs.model_dump_json()
    assert obs == asyncio.run(run_case(case, revision=revision))


def test_cases_are_isolated_and_sandbox_result_is_copy():
    lookup_customer("C-123")["tier"] = "modified"
    assert lookup_customer("C-123")["tier"] == "gold"
    case = Case(title="isolated", turns=[{"user": "What did you find?"}])
    assert "Please provide" in asyncio.run(run_case(case)).messages[-1]["content"]


@pytest.mark.parametrize("kwargs", [{"revision": "unknown"}, {"mode": "recorded"}, {"mode": "fake"}])
def test_invalid_registration(kwargs):
    case = Case(title="test", turns=[{"user": "hello"}])
    with pytest.raises(ValueError):
        asyncio.run(run_case(case, **kwargs))


def test_fixture_version_and_unknown_customer():
    case = Case(title="test", fixture_version="unknown", turns=[{"user": "Look up C-123"}])
    with pytest.raises(ValueError, match="fixture"):
        asyncio.run(run_case(case))
    case.fixture_version = "synthetic-v1"
    case.turns[0].user = "Look up C-000"
    obs = asyncio.run(run_case(case))
    assert obs.tool_calls[0].error
    assert evaluate(case, obs).gate == "error"


def test_redaction_before_observation():
    case = Case(title="test", turns=[{"user": "Look up C-123 api_key=secret-value"}])
    assert "secret-value" not in asyncio.run(run_case(case)).model_dump_json()


def test_synthetic_fixtures_are_candidates():
    cases = load_cases(FIXTURES / "cases.json")
    assert all(c.source["approved"] is False and c.source["review_status"] == "candidate" for c in cases)
    with (FIXTURES / "customer-lookup.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["question"] == "Look up customer C-123"


def test_demo_cli():
    assert main(["evaluate", str(FIXTURES / "cases.json")]) == 0
    assert main(["evaluate", str(FIXTURES / "cases.json"), "--revision", "buggy"]) == 1


def test_missing_live_config_is_clear(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    case = Case(title="live", turns=[{"user": "Look up C-123"}])
    with pytest.raises(ValueError, match="Live mode requires"):
        asyncio.run(run_case(case, mode="live"))


@pytest.mark.parametrize("collision", [False, True])
def test_demo_tool_result_keys_are_redacted_or_rejected(monkeypatch, collision):
    from goldenloop_demo_agent import agent

    customer = lookup_customer("C-123")
    customer["person@example.com"] = "synthetic annotation"
    if collision:
        customer["other@example.com"] = "another annotation"
    monkeypatch.setattr(agent, "lookup_customer", lambda customer_id: dict(customer))
    case = Case(title="privacy", turns=[{"user": "Look up C-123"}])
    if collision:
        with pytest.raises(ValueError, match="Redaction would produce duplicate dictionary keys"):
            asyncio.run(run_case(case))
    else:
        obs = asyncio.run(run_case(case))
        assert "person@example.com" not in obs.model_dump_json()
        assert obs.tool_calls[0].result["[REDACTED]"] == "synthetic annotation"
