import json
import time

import httpx
import pytest
from goldenloop_demo_agent import run_case
from goldenloop_eval import Case, Observation
from sqlalchemy import select

from goldenloop_api.config import clean, invocation_secrets
from goldenloop_api.db import Event, Run
from goldenloop_api.judging import JudgeSnapshot, evaluate_with_judge, normalized_judge_lineage

from .conftest import wait_for
from .test_release_and_judge import judged_release


@pytest.fixture
def cli_transport(monkeypatch):
    import azure.identity
    from azure.core.credentials import AccessToken

    token = "opaque-runtime-bearer-value"
    state = {"token": token, "requests": [], "closed": 0, "error": False, "close_error": False}

    class Credential:
        def get_token(self, *scopes, **kwargs):
            return AccessToken(token, int(time.time()) + 3600)

        def close(self):
            state["closed"] += 1
            if state["close_error"]:
                raise RuntimeError(token)

    def respond(request):
        state["requests"].append(request)
        assert request.headers["authorization"] == f"Bearer {token}"
        assert token not in request.content.decode()
        if state["error"]:
            return httpx.Response(400, json={"error": {"message": token, "code": "bad_request"}})
        return httpx.Response(200, json={
            "id": "response", "object": "chat.completion", "created": 0, "model": token,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({
                    "score": 1.0, "reason": token, "evidence": [token], "insufficient_evidence": False,
                }),
            }}],
        })

    real_client = httpx.Client

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(azure.identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.delenv("GOLDENLOOP_JUDGE_API_KEY", raising=False)
    return state


@pytest.mark.parametrize("error", [False, True])
async def test_wrapper_exposes_dynamic_secrets_to_sdk_and_caller(cli_transport, error):
    cli_transport["error"] = error
    token = cli_transport["token"]
    case = Case(title="Synthetic", turns=[{"user": "hello"}], checks=[
        {"kind": "content_contains", "config": {"value": "hello"}},
        {"kind": "judge", "config": {"rubric": "Correct", "threshold": 0.5}},
    ])
    observation = Observation(
        case_id=case.id, case_revision=case.revision, agent_revision="fixed", trace_complete=True,
        messages=[{"role": "assistant", "turn": 0, "content": f"hello {token}"}],
    )
    context = invocation_secrets.set(())
    try:
        result = await evaluate_with_judge(
            case, observation, {"model": "judge", "provider": "azure-openai"}, time.monotonic() + 10,
            JudgeSnapshot(endpoint="https://judge.example.com", deployment="judge", api_version="v1",
                          auth="azure_cli"),
        )
        assert result.gate == ("error" if error else "pass")
        # SDK must sanitize deterministic evidence generated BEFORE token acquisition.
        assert result.checks[0].evidence["answers"] == ["hello [REDACTED]"]
        assert token not in result.model_dump_json()
        assert token in observation.model_dump_json()  # The original model was not rewritten.
        assert token not in json.dumps(clean(observation.model_dump(mode="json")))
        assert token in invocation_secrets.get()
        assert cli_transport["closed"] == 1
    finally:
        invocation_secrets.reset(context)


@pytest.mark.parametrize("failure", [None, "provider", "cleanup"])
async def test_cli_tokens_redacted_before_runner_persistence(
    client, app, case_payload, monkeypatch, cli_transport, failure
):
    token = cli_transport["token"]
    cli_transport["error"] = failure == "provider"
    cli_transport["close_error"] = failure == "cleanup"
    object.__setattr__(app.state.settings, "allow_live_judge", True)
    for suffix, value in {"ENDPOINT": "https://judge.example.com", "DEPLOYMENT": "judge",
                          "API_VERSION": "v1", "AUTH": "azure_cli", "TEMPERATURE": "0"}.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + suffix, value)

    async def echo(case, **kwargs):
        observation = await run_case(case, **kwargs)
        observation.messages[-1]["content"] += " " + token
        observation.tool_calls[0].result["runtime_value"] = token
        return observation

    monkeypatch.setattr("goldenloop_api.runner.run_case", echo)
    # Also verify the caller has the token before persisting unexpected cleanup failures.
    fail_run = app.state.runner.fail_run

    async def fail_with_context(*args):
        assert token in invocation_secrets.get()
        return await fail_run(*args)

    monkeypatch.setattr(app.state.runner, "fail_run", fail_with_context)
    _, release = await judged_release(client, case_payload)
    response = await client.post("/api/v1/evaluation-runs", json={
        "release_id": release["id"], "agent_revision": "fixed", "judge": "azure",
        "idempotency_key": "cli-redaction",
    })
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    result = await wait_for(client, f"/api/v1/evaluation-runs/{run_id}")
    assert result["gate"] == ("error" if failure else "pass"), result
    assert result["status"] == ("failed" if failure == "cleanup" else "completed")
    assert cli_transport["closed"] == len(cli_transport["requests"]) == 1
    assert token not in json.dumps(result)
    async with app.state.db.sessions() as session:
        stored = await session.get(Run, run_id)
        assert token not in json.dumps(stored.results)
        if failure != "cleanup":
            observation = stored.results[0]["observation"]
            assert "[REDACTED]" in observation["messages"][-1]["content"]
            assert observation["tool_calls"][0]["result"]["runtime_value"] == "[REDACTED]"
        events = (await session.scalars(select(Event).where(Event.stream == "run:" + run_id))).all()
        assert events
        assert token not in json.dumps([event.payload for event in events])
    assert token not in invocation_secrets.get()  # No cross-task/global credential leakage.


@pytest.mark.parametrize("endpoint", ["http://judge.example.com", "https://judge.example.com/path",
                                      "https://judge.example.com/?key=value", "https://user@judge.example.com"])
def test_legacy_endpoint_normalization_rejects_invalid_origins(endpoint):
    lineage = {"selection": "azure", "endpoint": endpoint}
    original = dict(lineage)
    with pytest.raises(ValueError):
        normalized_judge_lineage(lineage)
    assert lineage == original
