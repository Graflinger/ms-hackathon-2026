import asyncio
import io
import json
import threading
import time
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from goldenloop_eval import Case, OpenAIJudge, evaluate
from goldenloop_eval.cli import load_bundle

from goldenloop_api.bootstrap import migrate
from goldenloop_api.db import CaseHead, CaseRevision, Run
from goldenloop_api.exports import MAX_RELEASE_BYTES, cases_bytes
from goldenloop_api.main import create_app

from .conftest import publish, wait_for
from .test_projects import publish_v2, setup_project


async def test_release_revision_selection_is_pinned(client, case_payload):
    case, original = await publish(client, case_payload)
    await client.put(
        f"/api/v1/cases/{case['id']}",
        json={"case": {**case, "title": "Revised"}, "expected_revision": 1, "reason": "Edit"},
    )
    await client.post(f"/api/v1/cases/{case['id']}/approve", json={"revision": 2, "reason": "Reviewed"})
    body = {"name": "Selection", "case_ids": [case["id"]], "expected_revisions": {case["id"]: 1}}
    response = await client.post("/api/v1/dataset-releases", json=body)
    assert response.status_code == 409
    assert len((await client.get("/api/v1/dataset-releases")).json()) == 1
    response = await client.post(
        "/api/v1/dataset-releases", json={**body, "expected_revisions": {case["id"]: 2}}
    )
    assert response.status_code == 201
    release = (await client.get(f"/api/v1/dataset-releases/{response.json()['id']}")).json()
    assert release["cases"][0]["revision"] == 2
    assert (await client.get(f"/api/v1/dataset-releases/{original['id']}")).json()["cases"][0][
        "revision"
    ] == 1


@pytest.mark.parametrize(
    "expected",
    [None, {}, {"different": 1}, {"CASE": 0}, {"CASE": True}, {"CASE": "1"}, {"CASE": 1, "extra": 1}],
)
async def test_release_revision_map_required_and_exact(client, case_payload, expected):
    case = (await client.post("/api/v1/cases", json={"case": case_payload})).json()["case"]
    body = {"name": "Release", "case_ids": [case["id"]]}
    if expected is not None:
        body["expected_revisions"] = {
            case["id"] if key == "CASE" else key: value for key, value in expected.items()
        }
    assert (await client.post("/api/v1/dataset-releases", json=body)).status_code == 422


async def test_export_at_real_publication_byte_limit(client, app, tmp_path):
    cases = [
        Case(
            id=f"boundary-{index}",
            title="Synthetic boundary",
            turns=[{"user": "C-123"}],
            checks=[{"kind": "content_contains", "config": {"value": "C-123"}}],
            source={"synthetic": True, "padding": [0] * 8000},
        )
        for index in range(80)
    ]
    remaining = MAX_RELEASE_BYTES - len(cases_bytes(cases))
    for index, case in enumerate(cases):
        case.context = "x" * (remaining // len(cases) + (index < remaining % len(cases)))
    assert len(cases_bytes(cases)) == MAX_RELEASE_BYTES
    # This is the old export encoding: accepted data used to exceed the SDK's 10 MiB reader.
    assert (
        len(json.dumps([case.model_dump(mode="json") for case in cases], indent=2).encode())
        > 10 * 1024 * 1024
    )
    async with app.state.db.write() as session:
        session.add_all([CaseHead(id=case.id, latest=1) for case in cases])
        await session.flush()
        session.add_all(
            [
                CaseRevision(
                    case_id=case.id,
                    revision=1,
                    payload=case.model_dump(mode="json"),
                    status="approved",
                    reviewer="local-synthetic-reviewer",
                    reason="Boundary test",
                )
                for case in cases
            ]
        )
    body = {
        "name": "At limit",
        "case_ids": [case.id for case in cases],
        "expected_revisions": {case.id: 1 for case in cases},
    }
    response = await client.post("/api/v1/dataset-releases", json=body)
    assert response.status_code == 201, response.text
    release_id = response.json()["id"]
    response = await client.get(f"/api/v1/dataset-releases/{release_id}/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("cases.json") == cases_bytes(cases)
        archive.extractall(tmp_path / "bundle")
    manifest, loaded = load_bundle(tmp_path / "bundle")
    assert len(loaded) == 80 and manifest.release_id == release_id
    async with app.state.db.write() as session:
        row = await session.get(CaseRevision, (cases[-1].id, 1))
        row.payload = {**row.payload, "context": row.payload["context"] + "x"}
    assert (await client.post("/api/v1/dataset-releases", json=body)).status_code == 422


async def test_sanitizer_valueerror_has_generic_response(client, monkeypatch):
    def collision(value, **kwargs):
        raise ValueError("Collision between private-person@example.com and secret-key")

    monkeypatch.setattr("goldenloop_api.config.sanitize", collision)
    response = await client.post(
        "/api/v1/cases", json={"case": {"title": "Synthetic", "turns": [{"user": "C-123"}]}}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Input cannot be safely sanitized"}
    assert (await client.get("/api/v1/cases")).json() == []


async def test_real_sdk_key_collision_rejected_without_leak(client):
    response = await client.post(
        "/api/v1/cases",
        json={
            "case": {
                "title": "Synthetic",
                "turns": [{"user": "C-123"}],
                "source": {"first@example.com": "a", "second@example.com": "b"},
            }
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Input cannot be safely sanitized"}
    assert (await client.get("/api/v1/cases")).json() == []


@pytest.fixture
def azure_judge(app, monkeypatch):
    object.__setattr__(app.state.settings, "allow_live_judge", True)
    monkeypatch.delenv("GOLDENLOOP_JUDGE_TEMPERATURE", raising=False)
    for key, value in {
        "ENDPOINT": "https://judge.example.openai.azure.com",
        "DEPLOYMENT": "judge-deployment",
        "API_VERSION": "2024-10-21",
        "API_KEY": "opaque-judge-credential",
        "AUTH": "api_key",
    }.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + key, value)
    monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", lambda name: object())
    state = {"calls": [], "snapshots": [], "closed": 0, "score": 0.9, "error": False}

    def create(**kwargs):
        state["calls"].append(kwargs)
        if state["error"]:
            raise ValueError("opaque-judge-credential private provider error")
        return SimpleNamespace(
            model="actual-judge-model",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        refusal=None,
                        content=json.dumps(
                            {
                                "score": state["score"],
                                "reason": "Reviewed synthetic answer",
                                "evidence": ["C-123"],
                                "insufficient_evidence": False,
                            }
                        ),
                    ),
                )
            ],
        )

    def close():
        state["closed"] += 1

    def factory(**_snapshot):
        state["snapshots"].append(_snapshot)
        transport = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=close
        )
        return OpenAIJudge(
            transport, model="judge-deployment", provider="azure-openai", temperature=_snapshot["temperature"]
        )

    monkeypatch.setattr("goldenloop_api.judging.OpenAIJudge.from_azure", factory)
    return state


async def judged_release(client, case_payload):
    case_payload["checks"].append(
        {"kind": "judge", "config": {"rubric": "Uses the requested synthetic customer ID", "threshold": 0.8}}
    )
    return await publish(client, case_payload)


@pytest.mark.parametrize("score,gate", [(0.9, "pass"), (0.3, "fail")])
@pytest.mark.parametrize("temperature", [None, "0", "default"])
async def test_real_sdk_judge_adapter_runs_and_pins_lineage(
    client, case_payload, azure_judge, score, gate, monkeypatch, temperature
):
    if temperature is not None:
        monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", temperature)
    azure_judge["score"] = score
    _, release = await judged_release(client, case_payload)
    body = {
        "release_id": release["id"],
        "agent_revision": "fixed",
        "judge": "azure",
        "idempotency_key": "judged",
    }
    response = await client.post("/api/v1/evaluation-runs", json=body)
    assert response.status_code == 202, response.text
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["status"] == "completed" and result["gate"] == gate, result
    lineage = result["lineage"]["judge"]
    assert lineage["mode"] == "live" and result["mode"] == "mock"
    assert lineage["endpoint"] == "https://judge.example.openai.azure.com"
    assert lineage["model"] == "judge-deployment"
    assert lineage["auth"] == "api_key"
    temperature_settings = {} if temperature == "default" else {"temperature": 0}
    assert lineage["settings"] == {**temperature_settings, "timeout": 60, "max_retries": 0}
    assert azure_judge["snapshots"][0]["temperature"] == (None if temperature == "default" else 0)
    request = azure_judge["calls"][0]
    assert {key: request[key] for key in ("temperature",) if key in request} == temperature_settings
    assert lineage["checks"][0]["threshold"] == 0.8
    assert lineage["prompt_version"] == "goldenloop-judge-v1"
    assert "opaque-judge-credential" not in json.dumps(result)
    assert (
        result["results"][0]["checks"][-1]["evidence"]["settings"]["response_model"] == "actual-judge-model"
    )
    assert len(azure_judge["calls"]) == 1 and azure_judge["closed"] == 1
    assert (await client.post("/api/v1/evaluation-runs", json=body)).json()["id"] == result["id"]
    assert (await client.post("/api/v1/evaluation-runs", json={**body, "judge": "none"})).status_code == 409


async def test_mock_without_judge_is_credential_free_even_with_opt_in(client, case_payload, azure_judge):
    _, release = await publish(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "no-judge"},
    )
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["gate"] == "pass" and not result["lineage"]["judge"]["configured"]
    assert azure_judge["calls"] == [] and azure_judge["closed"] == 0


@pytest.mark.parametrize(
    "missing",
    ["opt-in", "ENDPOINT", "DEPLOYMENT", "API_VERSION", "API_KEY", "dependencies", "unsafe-endpoint",
     "invalid-auth", "blank-key", "cli-dependencies", "cli-missing-namespace", "invalid-temperature"],
)
async def test_judge_preflight_rejects_without_persisting_run(
    client, app, case_payload, azure_judge, monkeypatch, missing
):
    if missing == "opt-in":
        object.__setattr__(app.state.settings, "allow_live_judge", False)
    elif missing == "dependencies":
        monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", lambda name: None)
    elif missing == "unsafe-endpoint":
        monkeypatch.setenv("GOLDENLOOP_JUDGE_ENDPOINT", "https://user:password@example.com?api_key=secret")
    elif missing == "invalid-auth":
        monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", "automatic")
    elif missing == "invalid-temperature":
        monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", "1")
    elif missing == "blank-key":
        monkeypatch.setenv("GOLDENLOOP_JUDGE_API_KEY", "   ")
    elif missing.startswith("cli-"):
        monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", "azure_cli")

        def find_spec(name):
            if name == "azure.identity":
                if missing == "cli-missing-namespace":
                    raise ModuleNotFoundError("No module named azure")
                return None
            return object()

        monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", find_spec)
    else:
        monkeypatch.delenv("GOLDENLOOP_JUDGE_" + missing)
    _, release = await judged_release(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision": "fixed",
            "judge": "azure",
            "idempotency_key": "bad-config",
        },
    )
    assert response.status_code == 409
    assert (await client.get("/api/v1/evaluation-runs")).json() == []
    assert azure_judge["calls"] == []


async def test_cli_judge_preflight_and_execution_without_key(client, case_payload, azure_judge, monkeypatch):
    monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", "azure_cli")
    monkeypatch.delenv("GOLDENLOOP_JUDGE_API_KEY")
    _, release = await judged_release(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={"release_id": release["id"], "agent_revision": "fixed", "judge": "azure",
              "idempotency_key": "cli-judge"},
    )
    assert response.status_code == 202, response.text
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["status"] == "completed" and result["gate"] == "pass", result
    assert result["lineage"]["judge"]["auth"] == "azure_cli"
    assert azure_judge["snapshots"] == [{
        "endpoint": "https://judge.example.openai.azure.com", "deployment": "judge-deployment",
        "api_version": "2024-10-21", "auth": "azure_cli", "api_key": None,
        "temperature": 0,
    }]
    assert azure_judge["closed"] == 1


@pytest.mark.parametrize("auth", ["api_key", "azure_cli"])
@pytest.mark.parametrize("temperature", [None, "0", "default"])
async def test_judge_export_is_metadata_only(
    client, app, case_payload, azure_judge, monkeypatch, auth, temperature
):
    path, _agent, revision = await setup_project(client)
    case_payload["checks"].append({"kind": "judge", "config": {"rubric": "Correct", "threshold": 0.5}})
    _, release = await publish_v2(client, path, case_payload)
    object.__setattr__(app.state.settings, "allow_live_judge", False)
    monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", auth)
    monkeypatch.delenv("GOLDENLOOP_JUDGE_API_KEY")
    if temperature is not None:
        monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", temperature)

    from goldenloop_api.exports import export_bundle_v2

    def export_with_changed_environment(*args):
        # Export must use the selected metadata, even if global settings change before serialization.
        monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", "0" if temperature == "default" else "default")
        return export_bundle_v2(*args)

    monkeypatch.setattr("goldenloop_api.registry.export_bundle_v2", export_with_changed_environment)

    def forbidden(*args, **kwargs):
        pytest.fail("Metadata export must not check dependencies or construct a credential/client")

    monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", forbidden)
    monkeypatch.setattr("goldenloop_api.judging.OpenAIJudge.from_azure", forbidden)
    response = await client.get(
        path + f"/dataset-releases/{release['id']}/export",
        params={"agent_revision_id": revision["id"], "mode": "mock", "judge": "azure"},
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["judge"]["auth"] == auth
        assert manifest["judge"]["settings"] == ({} if temperature == "default" else {"temperature": 0})
        assert "api_key" not in manifest["judge"]
        example = archive.read(".env.example").decode()
        assert f"GOLDENLOOP_JUDGE_AUTH={auth}\n" in example
        assert f"GOLDENLOOP_JUDGE_TEMPERATURE={temperature or '0'}\n" in example
        assert ("GOLDENLOOP_JUDGE_API_KEY=" in example) == (auth == "api_key")
        assert ("az login" in example) == (auth == "azure_cli")
        assert "goldenloop-eval[live]" in archive.read("requirements.txt").decode()
        for name in archive.namelist():
            assert b"opaque-judge-credential" not in archive.read(name)
    assert azure_judge["calls"] == azure_judge["snapshots"] == []


@pytest.mark.parametrize("pinned_auth,current_auth,legacy,pinned_temp,current_temp,passes", [
    (None, None, True, None, None, True),
    (None, "api_key", True, None, "0", True),
    (None, "azure_cli", True, None, None, False),
    ("api_key", "azure_cli", False, None, None, False),
    ("azure_cli", None, False, None, None, False),
    ("azure_cli", "azure_cli", False, None, None, True),
    (None, None, True, None, "default", False),
    ("api_key", "api_key", False, "0", "default", False),
    ("azure_cli", "azure_cli", False, "default", None, False),
    ("azure_cli", "azure_cli", False, "default", "default", True),
])
async def test_queued_judge_auth_drift_and_legacy_lineage(
    settings, case_payload, monkeypatch, pinned_auth, current_auth, legacy, pinned_temp, current_temp, passes
):
    await asyncio.to_thread(migrate, settings)
    object.__setattr__(settings, "allow_live_judge", True)
    for key, value in {"ENDPOINT": "https://judge.example.com", "DEPLOYMENT": "judge",
                       "API_VERSION": "v1", "API_KEY": "opaque-queued-key"}.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + key, value)
    monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", lambda name: object())
    if pinned_auth is None:
        monkeypatch.delenv("GOLDENLOOP_JUDGE_AUTH", raising=False)
    else:
        monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", pinned_auth)
    if pinned_temp is None:
        monkeypatch.delenv("GOLDENLOOP_JUDGE_TEMPERATURE", raising=False)
    else:
        monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", pinned_temp)
    snapshots = []

    async def score(case, observation, lineage, deadline, snapshot):
        snapshots.append(snapshot)
        return evaluate(case, observation, judge=lambda *args: {
            "score": 1.0, "reason": "Synthetic", "evidence": [], "provider": "azure-openai",
            "model": "judge", "prompt_version": "goldenloop-judge-v1", "mode": "live",
            "insufficient_evidence": False,
        })

    monkeypatch.setattr("goldenloop_api.runner.evaluate_with_judge", score)
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        _, release = await judged_release(client, case_payload)
        response = await client.post(
            "/api/v1/evaluation-runs",
            json={"release_id": release["id"], "agent_revision": "fixed", "judge": "azure",
                  "idempotency_key": "queued-auth"},
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        async with app.state.db.write() as session:
            run = await session.get(Run, run_id)
            assert run.lineage["judge"]["auth"] == (pinned_auth or "api_key")
            if legacy:
                judge = dict(run.lineage["judge"])
                judge.pop("auth")
                judge["endpoint"] += "/"
                run.lineage = {**run.lineage, "judge": judge}
            original_lineage = json.loads(json.dumps(run.lineage))
        if current_auth is None:
            monkeypatch.delenv("GOLDENLOOP_JUDGE_AUTH", raising=False)
        else:
            monkeypatch.setenv("GOLDENLOOP_JUDGE_AUTH", current_auth)
        if current_temp is None:
            monkeypatch.delenv("GOLDENLOOP_JUDGE_TEMPERATURE", raising=False)
        else:
            monkeypatch.setenv("GOLDENLOOP_JUDGE_TEMPERATURE", current_temp)
        async with app.router.lifespan_context(app):
            result = await wait_for(client, f"/api/v1/evaluation-runs/{run_id}")
            assert result["status"] == ("completed" if passes else "failed"), result
            assert result["gate"] == ("pass" if passes else "error"), result
            assert result["lineage"] == original_lineage
            assert len(snapshots) == int(passes)
            if passes:
                assert snapshots[0].endpoint == "https://judge.example.com"
                assert snapshots[0].auth == (pinned_auth or "api_key")
                assert snapshots[0].temperature == (None if pinned_temp == "default" else 0)
                assert snapshots[0].api_key == (None if pinned_auth == "azure_cli" else "opaque-queued-key")


async def test_judge_failure_is_safe_error_and_client_closed(client, case_payload, azure_judge):
    azure_judge["error"] = True
    _, release = await judged_release(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision": "fixed",
            "judge": "azure",
            "idempotency_key": "judge-error",
        },
    )
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["gate"] == "error" and "opaque-judge-credential" not in json.dumps(result)
    assert azure_judge["closed"] == 1


async def test_queued_judge_config_change_fails_before_cloud(settings, case_payload, monkeypatch):
    await asyncio.to_thread(migrate, settings)
    object.__setattr__(settings, "allow_live_judge", True)
    for key, value in {
        "ENDPOINT": "https://judge.example.com",
        "DEPLOYMENT": "initial",
        "API_VERSION": "v1",
        "API_KEY": "opaque",
    }.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + key, value)
    monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", lambda name: object())
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        _, release = await judged_release(client, case_payload)
        response = await client.post(
            "/api/v1/evaluation-runs",
            json={
                "release_id": release["id"],
                "agent_revision": "fixed",
                "judge": "azure",
                "idempotency_key": "changed",
            },
        )
        assert response.status_code == 202
    monkeypatch.setenv("GOLDENLOOP_JUDGE_DEPLOYMENT", "different")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client,
    ):
        result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
        assert result["status"] == "failed" and result["gate"] == "error"


async def test_judge_call_budget(client, app, case_payload, azure_judge):
    object.__setattr__(app.state.settings, "max_judge_calls", 1)
    case_payload["checks"].append(
        {"kind": "judge", "config": {"rubric": "Synthetic correctness", "threshold": 0.5}}
    )
    _, release = await judged_release(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision": "fixed",
            "judge": "azure",
            "idempotency_key": "budget",
        },
    )
    assert response.status_code == 422 and not azure_judge["calls"]


async def test_judge_cancel_records_immediately_and_closes_after_inflight_call(
    client, app, case_payload, azure_judge, monkeypatch
):
    entered, finish = threading.Event(), threading.Event()
    factory = OpenAIJudge.from_azure

    def slow_factory(**snapshot):
        judge = factory(**snapshot)
        create = judge.client.chat.completions.create

        def slow(**kwargs):
            entered.set()
            if not finish.wait(5):
                raise TimeoutError("Test judge did not finish")
            return create(**kwargs)

        judge.client.chat.completions.create = slow
        return judge

    monkeypatch.setattr("goldenloop_api.judging.OpenAIJudge.from_azure", slow_factory)
    case_payload["checks"].append({"kind": "judge", "config": {"rubric": "Second check", "threshold": 0.5}})
    _, release = await judged_release(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision": "fixed",
            "judge": "azure",
            "idempotency_key": "cancel-judge",
        },
    )
    run_id = response.json()["id"]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        response = await client.post(f"/api/v1/evaluation-runs/{run_id}/cancel")
        assert response.json()["status"] == "cancelled" and response.json()["gate"] == "error"
        await client.post(f"/api/v1/evaluation-runs/{run_id}/cancel")
        task = app.state.runner.tasks["run:" + run_id]
        assert not task.done() and azure_judge["closed"] == 0
    finally:
        finish.set()
    await asyncio.gather(task, return_exceptions=True)
    assert len(azure_judge["calls"]) == 1 and azure_judge["closed"] == 1
    assert (await client.get(f"/api/v1/evaluation-runs/{run_id}")).json()["status"] == "cancelled"


def test_compact_serialization_uses_utf8():
    case = Case(title="Synthetic " + chr(233), turns=[{"user": "C-123"}])
    assert chr(233).encode("utf-8") in cases_bytes([case])
    assert b"\\u00e9" not in cases_bytes([case])


async def test_judge_deadline_stops_later_checks_and_releases_client(
    client, app, case_payload, azure_judge, monkeypatch
):
    object.__setattr__(app.state.settings, "run_timeout", 0.1)
    factory = OpenAIJudge.from_azure

    def slow_factory(**snapshot):
        judge = factory(**snapshot)
        create = judge.client.chat.completions.create

        def slow(**kwargs):
            time.sleep(0.2)
            return create(**kwargs)

        judge.client.chat.completions.create = slow
        return judge

    monkeypatch.setattr("goldenloop_api.judging.OpenAIJudge.from_azure", slow_factory)
    case_payload["checks"].append({"kind": "judge", "config": {"rubric": "Second check", "threshold": 0.5}})
    _, release = await judged_release(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision": "fixed",
            "judge": "azure",
            "idempotency_key": "deadline-judge",
        },
    )
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["status"] == "failed" and result["gate"] == "error"
    assert len(azure_judge["calls"]) == 1 and azure_judge["closed"] == 1
