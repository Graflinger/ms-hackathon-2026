"""Exercise the installed Microsoft framework with fake model responses, not cloud calls."""
import asyncio
import builtins
import json
import os

import pytest

from goldenloop_demo_agent import run_case
from goldenloop_eval import Case, evaluate


def settings(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://synthetic.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_COMPLETION_MODEL", "unit-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-only-credential")


def test_missing_optional_dependency(monkeypatch):
    settings(monkeypatch)
    original = builtins.__import__
    def no_framework(name, *args, **kwargs):
        if name == "agent_framework":
            raise ImportError("simulated missing optional package")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_framework)
    case = Case(title="test", turns=[{"user": "Look up C-123"}])
    with pytest.raises(RuntimeError, match=r"Install goldenloop-demo-agent\[live\]"):
        asyncio.run(run_case(case, mode="live"))


@pytest.mark.parametrize("failure", [None, "provider", "tool", "malformed_arguments"])
def test_real_framework_loop_and_generated_history(monkeypatch, failure):
    pytest.importorskip("agent_framework.openai")
    import openai
    from openai.types.chat import ChatCompletion

    settings(monkeypatch)
    requests = []
    clients = []
    real_client = openai.AsyncAzureOpenAI

    # Keep the concrete SDK type intact: the provider uses isinstance for routing.
    original_init = real_client.__init__
    def patched_init(self, **kwargs):
        original_init(self, **kwargs)
        clients.append(self)
        async def create(**params):
            requests.append(params)
            if failure == "provider":
                raise RuntimeError("unit-only-credential")
            number = len(requests)
            if number == 1:
                arguments = json.dumps({"customer_id": "C-000" if failure == "tool" else "C-123"})
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function", "function": {
                        "name": "lookup_customer", "arguments": "not-json" if failure == "malformed_arguments" else arguments,
                    },
                }]}
                finish_reason = "tool_calls"
            else:
                message = {"role": "assistant", "content": f"Generated C-123 customer answer {number}."}
                finish_reason = "stop"
            return ChatCompletion.model_validate({
                "id": f"response-{number}", "object": "chat.completion", "created": 0,
                "model": "unit-deployment", "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            })
        monkeypatch.setattr(self.chat.completions, "create", create)
    monkeypatch.setattr(real_client, "__init__", patched_init)
    case = Case(title="test", turns=[
        {"user": "Look up customer C-123", "reference_answer": "REFERENCE NEVER IN HISTORY"},
        {"user": "Which customer was it?", "reference_answer": "REFERENCE NEVER IN HISTORY"},
    ], checks=[{"kind": "tool_arguments", "turn": 0, "config": {
        "tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123",
    }}])
    obs = asyncio.run(run_case(case, mode="live"))
    assert all(client.is_closed() for client in clients)
    assert "unit-only-credential" not in obs.model_dump_json()
    if failure:
        assert evaluate(case, obs).gate == "error"
    else:
        assert obs.error is None, obs.error
        assert obs.trace_complete
        assert obs.tool_calls[0].arguments == {"customer_id": "C-123"}
        assert "Synthetic Alpine Bikes" in str(obs.tool_calls[0].result)
        assert obs.tool_calls[0].id == "call-1"
        assert evaluate(case, obs).gate == "pass"
        history = json.dumps(requests[-1]["messages"])
        assert "Generated C-123 customer answer 2." in history
        assert "REFERENCE NEVER IN HISTORY" not in history
        assert obs.usage["total_token_count"] > 0
        assert obs.latency_ms >= 0


def test_insecure_endpoint_rejected(monkeypatch):
    settings(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "http://localhost")
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(run_case(Case(title="test", turns=[{"user": "hello"}]), mode="live"))


def fake_completion_transport(monkeypatch, responses):
    """Run the real AF/client loop against scripted HTTP completions or failures."""
    import httpx

    requests, clients = [], []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        response = responses[len(requests) - 1]
        if isinstance(response, Exception):
            raise response
        return httpx.Response(200, json={
            "id": f"response-{len(requests)}", "object": "chat.completion", "created": 0,
            "model": "unit-deployment", "choices": [{"index": 0, "message": response,
                "finish_reason": "tool_calls" if response.get("tool_calls") else "stop"}],
        })

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return requests, clients


@pytest.mark.parametrize("adapter", ["synthetic-customer", "synthetic-powerplant-var"])
@pytest.mark.parametrize(("outcome", "failure_turn"), [
    ("complete", 1), ("provider", 0), ("provider", 1), ("tool_then_provider", 0), ("tool_then_provider", 1),
])
def test_executed_tools_survive_provider_failure(monkeypatch, adapter, outcome, failure_turn):
    pytest.importorskip("agent_framework.openai")
    from goldenloop_demo_agent import live, powerplant

    settings(monkeypatch)
    secret = "unit-only-credential"
    executed = []
    customer = live.sandbox_lookup
    incidents = powerplant.similar_occurrences

    def lookup(customer_id):
        executed.append("lookup_customer")
        if outcome == "tool_then_provider":
            raise RuntimeError(f"Customer error {secret}")
        return {**customer("C-123"), "note": f"Observed {secret}"}

    def similar_occurrences(category: str) -> dict:
        """Return synthetic incidents for the requested category."""
        executed.append("similar_occurrences")
        if outcome == "tool_then_provider":
            raise RuntimeError(f"Incident error {secret}")
        return {**incidents(category), "note": f"Observed {secret}"}

    monkeypatch.setattr(live, "sandbox_lookup", lookup)
    monkeypatch.setattr(powerplant, "similar_occurrences", similar_occurrences)
    if adapter == "synthetic-customer":
        names = ["lookup_customer"]
        arguments = {"customer_id": f"C-123 {secret}"}
        fixture = "synthetic-v1"
    else:
        names = ["commercial_outlook", "risk_assessment", "similar_occurrences"]
        arguments = {"category": "cooling_water"}
        fixture = powerplant.FIXTURE_VERSION
    responses = []
    turns = 2 if outcome == "complete" else failure_turn + 1
    for turn in range(turns):
        responses.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call-{turn}-{secret}-{name}", "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }} for name in names
        ]})
        responses.append(RuntimeError(f"Provider error {secret}")
                         if outcome != "complete" and turn == failure_turn
                         else {"role": "assistant", "content": "Synthetic answer"})
    requests, clients = fake_completion_transport(monkeypatch, responses)
    case = Case(title="Captured tool evidence", fixture_version=fixture,
                turns=[{"user": "Look up the synthetic data"}, {"user": "Check again"}],
                checks=[{"kind": "tool_required", "config": {"tool": name}} for name in names])

    obs = asyncio.run(live.run_live(case, "fixed", adapter=adapter))

    assert len(requests) == 2 * turns
    assert all(client.is_closed for client in clients)
    assert len(executed) == turns
    assert len(obs.tool_calls) == len(names) * turns
    assert secret not in obs.model_dump_json()
    for turn in range(turns):
        calls = [call for call in obs.tool_calls if call.turn == turn]
        assert {call.tool for call in calls} == set(names)
        assert len(calls) == len(names)  # Middleware and response reconciliation must not duplicate calls.
        for call in calls:
            assert call.id == f"call-{turn}-[REDACTED]-{call.tool}"
            assert call.arguments == ({"customer_id": "C-123 [REDACTED]"}
                                      if adapter == "synthetic-customer" else arguments)
            assert call.latency_ms is not None and call.latency_ms > 0
            if outcome == "tool_then_provider" and call.tool == names[-1]:
                assert call.error == "Sandbox tool execution failed"
            else:
                assert call.error is None
                result = json.loads(call.result)
                assert result
                if call.tool == names[-1]:
                    assert result["note"] == "Observed [REDACTED]"
                elif call.tool == "commercial_outlook":
                    assert result["return"] in {"no", "low", "medium", "high"}
                else:
                    assert result["risk"] in {"none", "low", "medium", "high"}
    if outcome == "complete":
        assert obs.error is None
        assert obs.trace_complete
        assert evaluate(case, obs).gate == "pass"
    else:
        assert obs.error is not None
        assert not obs.trace_complete
        assert evaluate(case, obs).gate == "error"


@pytest.mark.parametrize("corruption", [
    "missing_trace", "missing_result", "unmatched_result", "duplicate_call", "duplicate_result", "missing_id",
    "invalid_arguments",
])
def test_captured_tools_do_not_bypass_response_validation(monkeypatch, corruption):
    af = pytest.importorskip("agent_framework")
    settings(monkeypatch)
    fake_completion_transport(monkeypatch, [
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1", "type": "function", "function": {
                "name": "lookup_customer", "arguments": '{"customer_id":"C-123"}',
            },
        }]},
        {"role": "assistant", "content": "Synthetic answer"},
    ])
    original_run = af.Agent.run

    async def corrupt_response(self, *args, **kwargs):
        response = await original_run(self, *args, **kwargs)
        for message in response.messages:
            for content in list(message.contents):
                if corruption == "missing_trace" and content.type in {"function_call", "function_result"}:
                    message.contents.remove(content)
                elif content.type == "function_result":
                    if corruption == "missing_result":
                        message.contents.remove(content)
                    elif corruption == "unmatched_result":
                        content.call_id = "unknown"
                    elif corruption == "duplicate_result":
                        message.contents.append(content)
                elif content.type == "function_call":
                    if corruption == "missing_id":
                        content.call_id = None
                    elif corruption == "duplicate_call":
                        message.contents.append(content)
                    elif corruption == "invalid_arguments":
                        content.arguments = '["not", "an", "object"]'
        return response

    monkeypatch.setattr(af.Agent, "run", corrupt_response)
    case = Case(title="Incomplete evidence", turns=[{"user": "Look up C-123"}],
                checks=[{"kind": "tool_required", "config": {"tool": "lookup_customer"}}])
    obs = asyncio.run(run_case(case, mode="live"))
    assert obs.error is not None
    assert not obs.trace_complete
    assert len(obs.tool_calls) == 1
    assert obs.tool_calls[0].id == "call-1"
    assert "Synthetic Alpine Bikes" in obs.tool_calls[0].result
    assert evaluate(case, obs).gate == "error"


def test_concurrent_explicit_live_transports_and_instructions(monkeypatch):
    import httpx
    from goldenloop_demo_agent import run_revision
    from goldenloop_eval import AgentConnection, AgentSpec

    settings(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "legacy-bearer-must-not-be-used")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Other-Service-Key: private-project-a-secret\napi-key: wrong-key\nAuthorization: Bearer wrong-auth")
    monkeypatch.setenv("OPENAI_ORG_ID", "private-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "private-project")
    before = dict(os.environ)
    requests, clients = [], []
    real_client = httpx.AsyncClient

    async def respond(request):
        await asyncio.sleep(0.01)
        requests.append(request)
        return httpx.Response(200, json={
            "id": "response", "object": "chat.completion", "created": 0, "model": "observed-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "Generated synthetic answer",
            }}],
        })

    class Client(real_client):
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    async def run():
        tasks = []
        for team in ("one", "two"):
            spec = AgentSpec(variant="fixed", modes=["live"], instructions=f"Instructions for {team}",
                             connection=AgentConnection(endpoint=f"https://{team}.openai.azure.com",
                                 deployment=f"model-{team}", api_version=f"version-{team}", auth="api_key", binding=team))
            tasks.append(run_revision(Case(title=team, turns=[{"user": "hello"}]), f"revision-{team}",
                                      spec, "live", api_key=f"credential-{team}"))
        return await asyncio.gather(*tasks)

    observations = asyncio.run(run())
    assert all(o.error is None for o in observations), observations
    assert len(requests) == 2
    for request in requests:
        team = request.url.host.split(".")[0]
        other = "two" if team == "one" else "one"
        assert request.headers["api-key"] == f"credential-{team}"
        assert "authorization" not in request.headers
        assert "x-other-service-key" not in request.headers
        assert "openai-organization" not in request.headers
        assert "openai-project" not in request.headers
        assert f"/deployments/model-{team}/" in request.url.path
        assert request.url.params["api-version"] == f"version-{team}"
        assert f"Instructions for {team}" in request.content.decode()
        assert f"Instructions for {other}" not in request.content.decode()
    assert all(c.is_closed for c in clients)
    assert dict(os.environ) == before


@pytest.mark.parametrize("auth", ["api_key", "azure_cli"])
def test_live_never_follows_credential_redirects(monkeypatch, auth):
    import httpx
    import azure.identity
    from goldenloop_demo_agent import run_revision
    from goldenloop_eval import AgentConnection, AgentSpec

    requests, clients, credentials = [], [], []
    real_client = httpx.AsyncClient
    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://unapproved.test/steal"})
    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)
    class Credential:
        closed = False
        def __init__(self):
            credentials.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(azure.identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(azure.identity, "get_bearer_token_provider", lambda credential, scope: lambda: "unit-token")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "wrong-global-key")
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "wrong-global-token")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Other-Service-Key: private-project-a-secret\nAuthorization: Bearer wrong-auth")
    monkeypatch.setenv("OPENAI_ORG_ID", "private-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "private-project")
    spec = AgentSpec(variant="fixed", modes=["live"], connection=AgentConnection(
        endpoint="https://approved.test", deployment="unit", api_version="unit", auth=auth, binding="team"))
    obs = asyncio.run(run_revision(Case(title="redirect", turns=[{"user": "hello"}]), "revision", spec,
                                   "live", api_key="unit-key" if auth == "api_key" else None))
    assert obs.error is not None
    assert len(requests) == 1
    assert requests[0].url.host == "approved.test"
    assert "x-other-service-key" not in requests[0].headers
    assert "openai-organization" not in requests[0].headers
    assert "openai-project" not in requests[0].headers
    if auth == "api_key":
        assert requests[0].headers["api-key"] == "unit-key"
    else:
        assert requests[0].headers["authorization"] == "Bearer unit-token"
        assert "api-key" not in requests[0].headers
    assert all(c.is_closed for c in clients)
    assert all(c.closed for c in credentials)
