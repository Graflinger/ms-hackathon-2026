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


def test_explicit_judge_blocks_redirects_and_legacy_auth(monkeypatch):
    import httpx
    import os
    requests = []
    real_client = httpx.Client
    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://unapproved.test/steal"})
    class Client(real_client):
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "legacy-token")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Other-Service-Key: private-project-a-secret\napi-key: wrong-key\nAuthorization: Bearer wrong-auth")
    monkeypatch.setenv("OPENAI_ORG_ID", "private-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "private-project")
    before = dict(os.environ)
    judge = OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge",
                                   api_version="unit", api_key="explicit-key")
    case = Case(title="judge", turns=[{"user": "hello"}], checks=[{
        "kind": "judge", "config": {"rubric": "accuracy", "threshold": 0.8}}])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="revision", trace_complete=True,
                      messages=[{"role": "assistant", "turn": 0, "content": "ok"}])
    try:
        assert evaluate(case, obs, judge).gate == "error"
    finally:
        judge.client.close()
    assert judge.client.is_closed()
    assert len(requests) == 1
    assert requests[0].url.host == "approved.test"
    assert requests[0].headers["api-key"] == "explicit-key"
    assert "authorization" not in requests[0].headers
    assert "x-other-service-key" not in requests[0].headers
    assert "openai-organization" not in requests[0].headers
    assert "openai-project" not in requests[0].headers
    assert dict(os.environ) == before


def test_judge_secret_snapshot_redacts_prompt_verdict_and_evaluation(monkeypatch):
    import httpx
    from goldenloop_eval import Check
    key = "opaque-private-project-value"
    requests = []
    real_client = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "response", "object": "chat.completion", "created": 0, "model": key,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"score": 0.9, "reason": key,
                    "evidence": [key], "insufficient_evidence": False}),
            }}],
        })

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    judge = OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge",
                                   api_version="unit", api_key=key)
    check = Check(id="judge", kind="judge", config={"rubric": key, "threshold": 0.8})
    case = Case(id="case", title="judge", context=key, source={key: key},
                turns=[{"user": key}], checks=[check])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="revision", trace_complete=True,
                      messages=[{"role": "assistant", "turn": 0, "content": key}])
    try:
        verdict = judge(case, obs, check)
        assert key not in verdict.model_dump_json()
        result = evaluate(case, obs, judge)
        assert result.gate == "pass"
        assert key not in result.model_dump_json()
        assert len(requests) == 2
        assert all(key not in r.content.decode() for r in requests)
        assert all(r.headers["api-key"] == key for r in requests)
        case.source["[REDACTED]"] = "collision"
        with pytest.raises(ValueError, match="duplicate"):
            judge(case, obs, check)
        assert evaluate(case, obs, judge).gate == "error"
        assert len(requests) == 2
    finally:
        judge.client.close()


def test_explicit_client_copies_do_not_reintroduce_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Other-Service-Key: private-secret")
    monkeypatch.setenv("OPENAI_ORG_ID", "private-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "private-project")
    judge = OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge",
                                   api_version="unit", api_key="explicit-key")
    try:
        copied = judge.client.with_options(timeout=10)
        assert copied._custom_headers == {}
        assert copied.organization is None and copied.project is None
        assert "private" not in str(copied.default_headers)
    finally:
        judge.client.close()


def test_unsupported_openai_version_fails_closed(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "__version__", "2.25.0")
    with pytest.raises(RuntimeError, match="openai>=3.8"):
        OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge",
                              api_version="unit", api_key="explicit-key")


def test_verdict_dictionary_redaction_collision_fails_closed():
    from goldenloop_eval import JudgeVerdict
    key = "opaque-private-value"
    def judge(*args):
        return JudgeVerdict(score=0.9, reason=key, evidence={key: "one", "[REDACTED]": "two"},
                            insufficient_evidence=False, provider="unit", model="unit",
                            prompt_version="unit", mode="mock")
    case = Case(title="judge", turns=[{"user": "hello"}], checks=[{
        "kind": "judge", "config": {"rubric": "accuracy", "threshold": 0.8}}])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="revision", trace_complete=True,
                      messages=[{"role": "assistant", "turn": 0, "content": "ok"}])
    result = evaluate(case, obs, judge, secrets=(key,))
    assert result.gate == "error"
    assert key not in result.model_dump_json()
