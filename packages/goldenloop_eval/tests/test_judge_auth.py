import json
import os
import time

import httpx
import pytest
from azure.core.credentials import AccessToken

from goldenloop_eval import (
    AgentSpec, BundleManifestV2, Case, Check, Observation, OpenAIJudge, agent_spec_hash,
    azure_judge_snapshot, azure_judge_snapshot_from_env, evaluate, release_hash,
)
from goldenloop_eval.cli import main


@pytest.fixture
def judge_env(monkeypatch):
    for suffix in ("AUTH", "API_KEY", "TEMPERATURE"):
        monkeypatch.delenv("GOLDENLOOP_JUDGE_" + suffix, raising=False)
    monkeypatch.setenv("GOLDENLOOP_JUDGE_ENDPOINT", "https://approved.test/")
    monkeypatch.setenv("GOLDENLOOP_JUDGE_DEPLOYMENT", "judge")
    monkeypatch.setenv("GOLDENLOOP_JUDGE_API_VERSION", "unit")


@pytest.fixture
def cli_credential(monkeypatch):
    import azure.identity

    class Credential:
        token = "opaque-cli-credential"

        def __init__(self):
            self.closed = 0
            self.scopes = []

        def get_token(self, *scopes, **kwargs):
            self.scopes.append(scopes)
            return AccessToken(self.token, int(time.time()) + 3600)

        def close(self):
            self.closed += 1

    credentials = []

    def create():
        credential = Credential()
        credentials.append(credential)
        return credential

    monkeypatch.setattr(azure.identity, "AzureCliCredential", create)
    return credentials


def case_and_observation(text="hello"):
    case = Case(id="case", title="judge", turns=[{"user": text}], checks=[{
        "id": "judge", "kind": "judge", "config": {"rubric": "accuracy", "threshold": 0.8}}])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="fixed", trace_complete=True,
                      messages=[{"role": "assistant", "turn": 0, "content": text}])
    return case, obs


def fake_http(monkeypatch, respond):
    clients = []
    real_client = httpx.Client

    class Client(real_client):
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx, "Client", Client)
    return clients


def score_response(text="Supported"):
    return httpx.Response(200, json={
        "id": "response", "object": "chat.completion", "created": 0, "model": text,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps({"score": 0.9, "reason": text,
                "evidence": [text], "insufficient_evidence": False}),
        }}],
    })


@pytest.mark.parametrize(("value", "settings"), [(None, {"temperature": 0}),
                                                 ("0", {"temperature": 0}), ("default", {})])
def test_temperature_env_request_and_verdict(monkeypatch, judge_env, value, settings):
    if value is not None:
        monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", value)
    monkeypatch.setenv("GOLDENLOOP_JUDGE_API_KEY", "explicit-key")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return score_response()

    fake_http(monkeypatch, respond)
    snapshot = azure_judge_snapshot_from_env()
    assert snapshot["settings"] == settings
    judge = OpenAIJudge.from_azure_env(expected=snapshot)
    case, obs = case_and_observation()
    try:
        verdict = judge(case, obs, case.checks[0])
        assert verdict.settings == {**settings, "response_model": "Supported"}
        assert len(requests) == 1
        assert {k: v for k, v in requests[0].items() if k == "temperature"} == settings
    finally:
        judge.close()


@pytest.mark.parametrize("value", ["", "0.0", " 0", "0 ", "Default", "null", "None", "1", "nan"])
def test_temperature_env_strict_validation(monkeypatch, judge_env, value):
    monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", value)
    with pytest.raises(ValueError, match="TEMPERATURE must"):
        azure_judge_snapshot_from_env()
    with pytest.raises(ValueError, match="TEMPERATURE must"):
        OpenAIJudge.from_azure_env()


@pytest.mark.parametrize("value", [False, True, "0", "default", -1, 0.5, 1, float("nan"), float("inf"), {}])
def test_temperature_python_interfaces_strict_validation(value):
    with pytest.raises(ValueError, match="temperature must"):
        azure_judge_snapshot(endpoint="https://approved.test", deployment="judge", api_version="unit",
                             temperature=value)
    with pytest.raises(ValueError, match="temperature must"):
        OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge", api_version="unit",
                               temperature=value, api_key="explicit-key")
    with pytest.raises(ValueError, match="temperature must"):
        OpenAIJudge(object(), model="judge", provider="unit", temperature=value)


@pytest.mark.parametrize(("value", "settings"), [(0, {"temperature": 0}), (0.0, {"temperature": 0}), (None, {})])
def test_temperature_constructor_and_snapshot(value, settings):
    from types import SimpleNamespace
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model="unit", choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            refusal=None, content=json.dumps({"score": 1.0, "reason": "ok", "evidence": [],
                                             "insufficient_evidence": False})))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAIJudge(client, model="judge", provider="unit", temperature=value)
    case, obs = case_and_observation()
    verdict = judge(case, obs, case.checks[0])
    assert verdict.settings == {**settings, "response_model": "unit"}
    assert {k: v for k, v in calls[0].items() if k == "temperature"} == settings
    assert azure_judge_snapshot(endpoint="https://approved.test", deployment="judge", api_version="unit",
                                temperature=value)["settings"] == settings


def test_unsupported_temperature_not_silently_retried(monkeypatch, judge_env):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"code": "unsupported_value", "param": "temperature",
                                                 "message": "Unsupported temperature"}})

    fake_http(monkeypatch, respond)
    judge = OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge", api_version="unit",
                                   api_key="explicit-key")
    case, obs = case_and_observation()
    try:
        assert evaluate(case, obs, judge).gate == "error"
        assert len(requests) == 1
        assert requests[0]["temperature"] == 0
    finally:
        judge.close()


@pytest.mark.parametrize("ambient", [False, True])
@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.parametrize("close_via_client", [False, True])
def test_cli_auth_headers_scope_redaction_and_cleanup(
    monkeypatch, judge_env, cli_credential, ambient, redirect, close_via_client,
):
    monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", "azure_cli")
    for name in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_AD_TOKEN", "GOLDENLOOP_JUDGE_API_KEY"):
        if ambient:
            monkeypatch.setenv(name, "unrelated-credential")
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "api-key: wrong\nAuthorization: Bearer wrong\nX-Secret: wrong")
    before = dict(os.environ)
    requests = []

    def respond(request):
        requests.append(request)
        if redirect:
            return httpx.Response(307, headers={"location": "https://unapproved.test/steal"})
        return score_response(cli_credential[0].token)

    clients = fake_http(monkeypatch, respond)
    judge = OpenAIJudge.from_azure_env()
    credential = cli_credential[0]
    case, obs = case_and_observation(credential.token)
    case.checks.insert(0, Check(id="content", kind="content_contains", config={"value": credential.token}))
    try:
        result = evaluate(case, obs, judge)
        assert result.gate == ("error" if redirect else "pass")
        assert credential.token not in result.model_dump_json()
        assert len(requests) == 1
        request = requests[0]
        assert request.url.host == "approved.test"
        assert request.headers["authorization"] == f"Bearer {credential.token}"
        assert "api-key" not in request.headers and "x-secret" not in request.headers
        assert credential.token not in request.content.decode()
        assert "unrelated-credential" not in str(request.headers)
        assert json.loads(request.content)["temperature"] == 0
        assert credential.scopes == [("https://cognitiveservices.azure.com/.default",)]
        copied = judge.client.with_options(timeout=10)
        assert copied._azure_ad_token is None
        assert copied._azure_ad_token_provider is judge.client._azure_ad_token_provider
        assert copied._custom_headers == {}
    finally:
        (judge.client.close if close_via_client else judge.close)()
        judge.close()
    assert credential.closed == 1
    assert clients[0].is_closed
    assert dict(os.environ) == before


@pytest.mark.parametrize("auth", [None, "api_key", "", "unknown"])
def test_no_missing_key_fallback_or_unknown_auth(monkeypatch, judge_env, cli_credential, auth):
    if auth is not None:
        monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", auth)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "ambient-key")
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "ambient-token")
    with pytest.raises(ValueError, match="requires|auth must"):
        OpenAIJudge.from_azure_env()
    with pytest.raises(ValueError, match="requires|auth must"):
        OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge", api_version="unit",
                               **({"auth": auth} if auth is not None else {}))
    assert cli_credential == []


def test_cli_auth_rejects_explicit_key(judge_env, cli_credential):
    with pytest.raises(ValueError, match="must not receive"):
        OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge", api_version="unit",
                               auth="azure_cli", api_key="unrelated-key")
    assert cli_credential == []


def test_api_key_auth_does_not_depend_on_azure_identity(monkeypatch, judge_env):
    import builtins
    original = builtins.__import__

    def no_identity(name, *args, **kwargs):
        if name.startswith("azure.identity"):
            raise ImportError("unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_identity)
    monkeypatch.setenv("GOLDENLOOP_JUDGE_API_KEY", "explicit-key")
    judge = OpenAIJudge.from_azure_env()
    judge.close()


@pytest.mark.parametrize("stage", ["provider", "http", "openai", "judge"])
def test_construction_failure_closes_owned_resources(monkeypatch, judge_env, cli_credential, stage):
    import azure.identity
    import goldenloop_eval.azure_clients

    def fail(*args, **kwargs):
        raise RuntimeError("construction failed")

    clients = fake_http(monkeypatch, lambda request: score_response())
    if stage == "provider":
        monkeypatch.setattr(azure.identity, "get_bearer_token_provider", fail)
    elif stage == "http":
        monkeypatch.setattr(httpx, "Client", fail)
    elif stage == "openai":
        monkeypatch.setattr(goldenloop_eval.azure_clients, "ExplicitAzureOpenAI", fail)
    else:
        monkeypatch.setattr(OpenAIJudge, "__init__", fail)
    with pytest.raises(RuntimeError, match="construction failed"):
        OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge", api_version="unit",
                               auth="azure_cli")
    assert cli_credential[0].closed == 1
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("temperature", ["0", "default"])
def test_v2_bundle_cli_judge_real_transport_and_cleanup(
    tmp_path, monkeypatch, capsys, judge_env, cli_credential, temperature,
):
    import goldenloop_demo_agent
    monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", "azure_cli")
    monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", temperature)
    case, obs = case_and_observation()
    spec = AgentSpec(variant="fixed")
    manifest = BundleManifestV2(
        project_id="p", release_id="r", content_hash=release_hash([case]),
        agent_id="a", agent_revision="fixed", agent_spec=spec,
        agent_spec_hash=agent_spec_hash(spec), mode="mock", judge=azure_judge_snapshot_from_env(),
    )
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    (tmp_path / "cases.json").write_text(json.dumps([case.model_dump()]))

    async def invoke(*args, **kwargs):
        return obs

    monkeypatch.setattr(goldenloop_demo_agent, "run_revision", invoke)
    requests = []

    def respond(request):
        requests.append(request)
        return score_response(cli_credential[0].token)

    clients = fake_http(monkeypatch, respond)
    assert main(["bundle", str(tmp_path), "--judge", "azure"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["judge"] == manifest.judge
    assert cli_credential[0].token not in output
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert {k: v for k, v in payload.items() if k == "temperature"} == manifest.judge["settings"]
    assert "api-key" not in requests[0].headers
    assert cli_credential[0].closed == 1
    assert clients[0].is_closed


def test_credential_closed_even_when_transport_close_fails(monkeypatch, judge_env, cli_credential):
    from openai import AzureOpenAI
    judge = OpenAIJudge.from_azure(endpoint="https://approved.test", deployment="judge", api_version="unit",
                                   auth="azure_cli")
    original = AzureOpenAI.close

    def fail(client):
        original(client)
        raise RuntimeError("close failed")

    monkeypatch.setattr(AzureOpenAI, "close", fail)
    with pytest.raises(RuntimeError, match="close failed"):
        judge.close()
    assert cli_credential[0].closed == 1


@pytest.mark.parametrize("auth", ["api_key", "azure_cli"])
def test_snapshot_helpers_explicit_auth_without_credentials(monkeypatch, judge_env, auth):
    monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", auth)
    snapshot = azure_judge_snapshot_from_env()
    assert snapshot == azure_judge_snapshot(endpoint="https://approved.test/", deployment="judge",
                                           api_version="unit", auth=auth)
    assert snapshot["auth"] == auth
    assert snapshot["settings"] == {"temperature": 0}


@pytest.mark.parametrize("failure", [None, "token", "request", "configuration"])
def test_cli_reports_and_cleanup_with_real_cli_judge(
    tmp_path, monkeypatch, capsys, judge_env, cli_credential, failure,
):
    monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", "azure_cli")
    token = "opaque-cli-credential"
    case, obs = case_and_observation(token)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([case.model_dump()]))
    requests = []

    def respond(request):
        requests.append(request)
        if failure == "request":
            return httpx.Response(401, json={"error": {"message": token}})
        return score_response(token)

    clients = fake_http(monkeypatch, respond)

    async def runner(case, **kwargs):
        return obs

    if failure == "token":
        import azure.identity

        def fail_provider(*args):
            def fail():
                raise RuntimeError(token)
            return fail

        monkeypatch.setattr(azure.identity, "get_bearer_token_provider", fail_provider)
    args = ["evaluate", str(path), "--judge", "azure", "--json", str(tmp_path / "report.json"),
            "--junit", str(tmp_path / "report.xml")]
    if failure == "configuration":
        args += ["--adapter", "does_not_exist:run"]
    assert main(args, runner=runner) == (2 if failure else 0)
    outputs = [capsys.readouterr().out, (tmp_path / "report.json").read_text(),
               (tmp_path / "report.xml").read_text(), *(r.content.decode() for r in requests)]
    assert all(token not in output for output in outputs)
    assert cli_credential[0].closed == 1
    assert all(client.is_closed for client in clients)
