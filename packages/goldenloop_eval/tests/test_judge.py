import json
from types import SimpleNamespace

import pytest

from goldenloop_eval import Case, Observation, OpenAIJudge, evaluate


def test_openai_adapter_structured_output_and_lineage():
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        output = json.dumps({"score": 0.9, "reason": "Supported", "evidence": ["ok"], "insufficient_evidence": False})
        return SimpleNamespace(model="unit-model-snapshot", choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=output, refusal=None))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAIJudge(client, model="unit-model", provider="unit-fixture", mode="mock")
    case = Case(title="judge", turns=[{"user": "hello"}], checks=[{
        "kind": "judge", "config": {"rubric": "accuracy", "threshold": 0.8}}])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="fixed", trace_complete=True,
                      messages=[{"role": "assistant", "turn": 0, "content": "ok"}])
    result = evaluate(case, obs, judge)
    assert result.gate == "pass"
    assert result.checks[0].evidence["mode"] == "mock"
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in calls[0]
    assert "untrusted" in calls[0]["messages"][0]["content"]


def test_missing_azure_judge_config(monkeypatch):
    monkeypatch.delenv("GOLDENLOOP_JUDGE_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="Azure judge requires"):
        OpenAIJudge.from_azure_env()
