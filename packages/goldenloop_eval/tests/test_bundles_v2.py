import asyncio
import builtins
import json
import os

import pytest

from goldenloop_eval import AgentConnection, AgentSpec, BundleManifestV2, Case, agent_spec_hash, release_hash, run_bundle
from goldenloop_eval.cli import load_bundle, main


def bundle(tmp_path, *, variant="fixed", judge=None, live=False):
    case = Case(id="case", title="Synthetic", turns=[{"user": "Look up C-123"}], checks=[{
        "id": "tool", "kind": "tool_arguments",
        "config": {"tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123"},
    }])
    connection = AgentConnection(endpoint="https://unit.openai.azure.com", deployment="unit",
                                 api_version="2025-01-01-preview", auth="api_key", binding="team") if live else None
    spec = AgentSpec(variant=variant, modes=["live"] if live else ["mock"], connection=connection)
    manifest = BundleManifestV2(project_id="p", release_id="r", content_hash=release_hash([case]),
                                agent_id="a", agent_revision="immutable-revision", agent_spec=spec,
                                agent_spec_hash=agent_spec_hash(spec), mode="live" if live else "mock",
                                judge=judge or {"selection": "none"})
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps([case.model_dump(mode="json")]), encoding="utf-8")
    return manifest


@pytest.mark.parametrize(("variant", "gate", "code"), [("fixed", "pass", 0), ("buggy", "fail", 1)])
def test_v2_primitive_and_cli(tmp_path, capsys, variant, gate, code):
    manifest = bundle(tmp_path, variant=variant)
    report = asyncio.run(run_bundle(tmp_path))
    assert report["gate"] == gate
    assert report["agent_spec_hash"] == manifest.agent_spec_hash
    assert report["evaluations"][0]["agent_revision"] == "immutable-revision"
    assert main(["bundle", str(tmp_path)]) == code
    assert json.loads(capsys.readouterr().out)["sdk_version"] == "0.2.0"
    assert main(["bundle", str(tmp_path), "--adapter", "goldenloop_demo_agent:run_revision"]) == code
    assert main(["bundle", str(tmp_path), "--adapter", "goldenloop_demo_agent:run_case"]) == 2


def test_v2_tamper(tmp_path):
    bundle(tmp_path)
    path = tmp_path / "manifest.json"
    data = json.loads(path.read_text())
    data["agent_spec"]["instructions"] = "changed"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="spec hash mismatch"):
        load_bundle(tmp_path)
    bundle(tmp_path)
    path = tmp_path / "cases.json"
    data = json.loads(path.read_text())
    data[0]["title"] = "changed"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="content hash mismatch"):
        load_bundle(tmp_path)


def test_missing_adapter_fails_without_backend(tmp_path, monkeypatch):
    bundle(tmp_path)
    def unavailable(name):
        raise ImportError("not installed")
    monkeypatch.setattr("goldenloop_eval.bundles.importlib.import_module", unavailable)
    with pytest.raises(ImportError):
        asyncio.run(run_bundle(tmp_path))


def azure_judge():
    return {"selection": "azure", "provider": "azure-openai", "endpoint": "https://judge.openai.azure.com",
            "deployment": "judge", "api_version": "2025-01-01-preview", "prompt_version": "goldenloop-judge-v1",
            "settings": {"temperature": 0}}


def test_pinned_judge_requires_opt_in_credentials_and_exact_configuration(tmp_path, monkeypatch):
    bundle(tmp_path, judge=azure_judge())
    with pytest.raises(ValueError, match="selection"):
        asyncio.run(run_bundle(tmp_path))
    monkeypatch.delenv("GOLDENLOOP_JUDGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="requires"):
        asyncio.run(run_bundle(tmp_path, "azure"))
    for suffix, value in {"ENDPOINT": "https://different.test", "DEPLOYMENT": "judge",
                          "API_VERSION": "2025-01-01-preview", "API_KEY": "judge-key"}.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + suffix, value)
    before = dict(os.environ)
    with pytest.raises(ValueError, match="differs"):
        asyncio.run(run_bundle(tmp_path, "azure"))
    assert dict(os.environ) == before


def test_pinned_judge_matching_config_is_independent_and_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from goldenloop_eval import OpenAIJudge
    bundle(tmp_path, judge=azure_judge())
    for suffix, value in {"ENDPOINT": "https://judge.openai.azure.com/", "DEPLOYMENT": "judge",
                          "API_VERSION": "2025-01-01-preview", "API_KEY": "judge-key"}.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + suffix, value)
    calls, closed = [], []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(client=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(OpenAIJudge, "from_azure", create)
    before = dict(os.environ)
    report = asyncio.run(run_bundle(tmp_path, "azure"))
    assert report["gate"] == "pass"
    assert calls == [{"endpoint": "https://judge.openai.azure.com", "deployment": "judge",
                      "api_version": "2025-01-01-preview", "api_key": "judge-key"}]
    assert closed == [True]
    assert dict(os.environ) == before


def test_live_export_resolves_local_binding_once(tmp_path, monkeypatch):
    import goldenloop_demo_agent
    bundle(tmp_path, live=True)
    monkeypatch.setenv("UNIT_KEY", "local-key")
    monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", json.dumps({"team": {
        "endpoint": "https://unit.openai.azure.com", "auth": "api_key", "key_env": "UNIT_KEY", "projects": ["p"],
    }}))
    calls = []
    async def fake(case, revision_id, spec, mode, *, api_key):
        calls.append((revision_id, spec, mode, api_key))
        obs = await goldenloop_demo_agent.run_case(case)
        return obs.model_copy(update={"agent_revision": revision_id, "mode": mode})
    monkeypatch.setattr(goldenloop_demo_agent, "run_revision", fake)
    report = asyncio.run(run_bundle(tmp_path))
    assert report["gate"] == "pass"
    assert calls[0][2:] == ("live", "local-key")
    assert "local-key" not in json.dumps(report)
    monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", "{}")
    with pytest.raises(ValueError, match="authorize"):
        asyncio.run(run_bundle(tmp_path))


def test_pure_scoring_import_does_not_require_adapter_or_live_dependencies(monkeypatch):
    original = builtins.__import__
    def no_optional(name, *args, **kwargs):
        if name.startswith(("goldenloop_demo_agent", "goldenloop_api", "openai", "agent_framework")):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_optional)
    from goldenloop_eval import evaluate
    assert callable(evaluate)


@pytest.mark.parametrize("invalid", ["judge", "turn", "kind", "schema", "operator", "range",
                                     "no_required", "fixture", "multi_turn", "trace"])
def test_entire_bundle_preflight_precedes_any_invocation(tmp_path, monkeypatch, invalid):
    import goldenloop_demo_agent
    manifest = bundle(tmp_path, live=True)
    _, cases = load_bundle(tmp_path)
    bad = cases[0].model_copy(deep=True)
    bad.id = "later-case"
    if invalid == "judge":
        bad.checks[0].kind = "judge"
        bad.checks[0].config = {"rubric": "accuracy", "threshold": 0.8}
    elif invalid == "turn":
        bad.checks[0].turn = 5
    elif invalid == "kind":
        bad.checks[0].kind = "unknown"
    elif invalid == "schema":
        bad.checks[0].kind = "json_schema"
        bad.checks[0].config = {"schema": {"pattern": "unsafe"}}
    elif invalid == "operator":
        bad.checks[0].config["operator"] = "unknown"
    elif invalid == "range":
        bad.checks[0].config.update(operator="range", value={"min": 5, "max": 1})
    elif invalid == "no_required":
        bad.checks[0].required = False
    elif invalid == "fixture":
        bad.fixture_version = "unknown"
    elif invalid == "multi_turn":
        bad.turns.append(bad.turns[0].model_copy())
        manifest.agent_spec.supports_multi_turn = False
    elif invalid == "trace":
        cases[0].checks[0].kind = "content_contains"
        cases[0].checks[0].config = {"value": "synthetic"}
        manifest.agent_spec.trace_available = False
    cases.append(bad)
    manifest.content_hash = release_hash(cases)
    manifest.agent_spec_hash = agent_spec_hash(manifest.agent_spec)
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    (tmp_path / "cases.json").write_text(json.dumps([c.model_dump() for c in cases]))
    calls = []
    async def invoke(*args, **kwargs):
        calls.append("invoke")
        raise AssertionError("Must not invoke")
    def binding(*args):
        calls.append("binding")
        raise AssertionError("Must not resolve binding")
    monkeypatch.setattr(goldenloop_demo_agent, "run_revision", invoke)
    monkeypatch.setattr(goldenloop_demo_agent, "resolve_binding", binding)
    with pytest.raises(ValueError, match="preflight"):
        asyncio.run(run_bundle(tmp_path))
    assert calls == []


@pytest.mark.parametrize("collision", [False, True])
def test_bundle_secret_snapshot_redacts_cli_json_junit_and_agent_input(tmp_path, monkeypatch, capsys, collision):
    import httpx
    import goldenloop_demo_agent
    from goldenloop_eval import Check
    judge_key, agent_key = "opaque-judge-credential", "opaque-agent-credential"
    manifest = bundle(tmp_path, live=True, judge=azure_judge())
    _, cases = load_bundle(tmp_path)
    case = cases[0]
    case.context = f"{judge_key} {agent_key}"
    case.source = {judge_key: agent_key}
    if collision:
        case.source["[REDACTED]"] = "collision"
    case.checks.append(Check(id="judge", kind="judge", config={"rubric": case.context, "threshold": 0.8}))
    manifest.content_hash = release_hash(cases)
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    (tmp_path / "cases.json").write_text(json.dumps([c.model_dump() for c in cases]))
    source_bytes = (tmp_path / "cases.json").read_bytes()
    for suffix, value in {"ENDPOINT": "https://judge.openai.azure.com", "DEPLOYMENT": "judge",
                          "API_VERSION": "2025-01-01-preview", "API_KEY": judge_key}.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + suffix, value)
    monkeypatch.setenv("UNIT_KEY", agent_key)
    monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", json.dumps({"team": {
        "endpoint": "https://unit.openai.azure.com", "auth": "api_key", "key_env": "UNIT_KEY", "projects": ["p"],
    }}))
    inputs, requests, clients = [], [], []
    async def invoke(case, revision_id, spec, mode, *, api_key):
        inputs.append(case.model_dump_json())
        assert api_key == agent_key
        obs = await goldenloop_demo_agent.run_case(case)
        obs.messages[-1]["content"] += f" {judge_key} {agent_key}"
        return obs.model_copy(update={"agent_revision": revision_id, "mode": mode})
    monkeypatch.setattr(goldenloop_demo_agent, "run_revision", invoke)
    real_client = httpx.Client
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "response", "object": "chat.completion", "created": 0, "model": agent_key,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"score": 0.9,
                    "reason": f"{judge_key} {agent_key}", "evidence": [judge_key, agent_key], "insufficient_evidence": False}),
            }}],
        })
    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)
    monkeypatch.setattr(httpx, "Client", Client)
    args = ["bundle", str(tmp_path), "--judge", "azure", "--json", str(tmp_path / "report.json"),
            "--junit", str(tmp_path / "report.xml")]
    assert main(args) == (2 if collision else 0)
    outputs = [capsys.readouterr().out, (tmp_path / "report.json").read_text(),
               (tmp_path / "report.xml").read_text(), *inputs, *(r.content.decode() for r in requests)]
    assert all(judge_key not in output and agent_key not in output for output in outputs)
    assert len(inputs) == len(requests) == (0 if collision else 1)
    assert all(c.is_closed for c in clients)
    assert (tmp_path / "cases.json").read_bytes() == source_bytes
    if not collision:
        assert json.loads(outputs[1])["content_hash"] == manifest.content_hash


def test_opaque_secret_in_pinned_spec_rejected_not_rehashed(tmp_path, monkeypatch):
    import goldenloop_demo_agent
    key = "opaque-agent-credential"
    manifest = bundle(tmp_path, live=True)
    manifest.agent_spec.instructions = key
    manifest.agent_spec_hash = agent_spec_hash(manifest.agent_spec)
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json())
    monkeypatch.setattr(goldenloop_demo_agent, "resolve_binding", lambda *args: key)
    async def invoke(*args, **kwargs):
        raise AssertionError("Must not invoke")
    monkeypatch.setattr(goldenloop_demo_agent, "run_revision", invoke)
    with pytest.raises(ValueError, match="unsafe execution") as exc:
        asyncio.run(run_bundle(tmp_path))
    assert key not in str(exc.value)
