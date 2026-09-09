import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import httpx
from goldenloop_demo_agent import run_case
from goldenloop_eval import OpenAIJudge

from goldenloop_api.bootstrap import migrate
from goldenloop_api.main import create_app

from .conftest import wait_for
from .test_projects import DEMO, publish_v2, setup_project


async def test_live_snapshots_are_project_specific_stable_and_redacted(
    client, app, case_payload, monkeypatch
):
    object.__setattr__(app.state.settings, "allow_live", True)
    monkeypatch.setattr("goldenloop_api.registry.importlib.util.find_spec", lambda name: object())
    bindings = {}
    expected = {}
    projects = []
    for number in range(2):
        path, agent, _revision = await setup_project(client, f"Project {number}")
        project_id = path.rsplit("/", 1)[-1]
        binding = f"binding-{number}"
        key = f"opaque-provider-value-{number}"
        env_name = f"OPAQUE_{number}"
        monkeypatch.setenv(env_name, key)
        connection = {
            "endpoint": f"https://provider-{number}.example.com",
            "auth": "api_key",
            "binding": binding,
            "deployment": f"deployment-{number}",
            "api_version": "v1",
        }
        bindings[binding] = {
            "endpoint": connection["endpoint"],
            "auth": "api_key",
            "projects": [project_id],
            "key_env": env_name,
        }
        monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", json.dumps(bindings))
        response = await client.post(
            path + f"/agents/{agent['id']}/revisions",
            json={"label": "Live", "spec": {"variant": "fixed", "modes": ["live"], "connection": connection}},
        )
        assert response.status_code == 201, response.text
        revision = response.json()
        expected[revision["id"]] = (connection, key, env_name)
        cases = []
        for index in range(2):
            response = await client.post(
                path + "/cases", json={"case": {**case_payload, "title": f"Case {index}"}}
            )
            assert response.status_code == 201, response.text
            case = response.json()["case"]
            await client.post(
                path + f"/cases/{case['id']}/approve", json={"revision": 1, "reason": "Reviewed"}
            )
            cases.append(case)
        response = await client.post(
            path + "/dataset-releases",
            json={
                "name": "Pair",
                "case_ids": [c["id"] for c in cases],
                "expected_revisions": {c["id"]: 1 for c in cases},
            },
        )
        assert response.status_code == 201, response.text
        projects.append((path, revision, response.json()))
    observed = []

    async def invoke(case, revision_id, spec, mode, *, api_key):
        connection, key, env_name = expected[revision_id]
        assert spec.connection.model_dump(mode="json") == connection and api_key == key
        observed.append((revision_id, api_key))
        # Neither a key rotation nor changed global Azure defaults may switch an active run.
        monkeypatch.setenv(env_name, "rotated")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://unrelated.example.com")
        result = await run_case(case, revision="fixed", mode="mock")
        result.messages[-1]["content"] += " " + key
        return result.model_copy(update={"agent_revision": revision_id, "mode": mode})

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    for path, revision, release in projects:
        response = await client.post(
            path + "/evaluation-runs",
            json={
                "release_id": release["id"],
                "agent_revision_id": revision["id"],
                "mode": "live",
                "idempotency_key": "same-project-local-key",
            },
        )
        assert response.status_code == 202, response.text
        result = await wait_for(client, path + "/evaluation-runs/" + response.json()["id"])
        assert result["gate"] == "pass", result
        assert expected[revision["id"]][1] not in json.dumps(result)
        events = await client.get(path + f"/evaluation-runs/{result['id']}/events")
        assert expected[revision["id"]][1] not in events.text
    assert len(observed) == 4


async def test_queued_work_finishes_archived_and_missing_binding_fails_closed(
    settings, case_payload, monkeypatch
):
    await asyncio.to_thread(migrate, settings)
    app = create_app(settings)
    object.__setattr__(settings, "allow_live", True)
    monkeypatch.setattr("goldenloop_api.registry.importlib.util.find_spec", lambda name: object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        path, agent, revision = await setup_project(client)
        _, release = await publish_v2(client, path, case_payload)
        body = {
            "release_id": release["id"],
            "agent_revision_id": revision["id"],
            "mode": "mock",
            "idempotency_key": "queued",
        }
        queued = (await client.post(path + "/evaluation-runs", json=body)).json()
        cancelled = (
            await client.post(path + "/evaluation-runs", json={**body, "idempotency_key": "cancel"})
        ).json()
        chat = (await client.post(path + "/chat-sessions", json={"agent_revision_id": revision["id"]})).json()
        await client.post(path + f"/chat-sessions/{chat['id']}/messages", json={"content": "C-123"})
        connection = {
            "endpoint": "https://approved.example.com",
            "auth": "api_key",
            "binding": "approved",
            "deployment": "model",
            "api_version": "v1",
        }
        monkeypatch.setenv(
            "GOLDENLOOP_CONNECTION_BINDINGS",
            json.dumps(
                {
                    "approved": {
                        "endpoint": connection["endpoint"],
                        "auth": "api_key",
                        "key_env": "OPAQUE",
                        "projects": [agent["project_id"]],
                    }
                }
            ),
        )
        monkeypatch.setenv("OPAQUE", "opaque-available-provider-value")
        response = await client.post(
            path + f"/agents/{agent['id']}/revisions",
            json={"label": "Live", "spec": {"variant": "fixed", "modes": ["live"], "connection": connection}},
        )
        assert response.status_code == 201, response.text
        live = response.json()
        response = await client.post(
            path + "/evaluation-runs",
            json={
                **body,
                "mode": "live",
                "agent_revision_id": live["id"],
                "idempotency_key": "missing-binding",
            },
        )
        assert response.status_code == 202, response.text
        live_run = response.json()
        monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", "{}")
        await client.patch(path + f"/agents/{agent['id']}", json={"archived": True})
        await client.patch(path, json={"archived": True})
        assert (await client.post(path + f"/evaluation-runs/{cancelled['id']}/cancel")).status_code == 200
        assert (
            await client.post(path + "/evaluation-runs", json={**body, "idempotency_key": "blocked"})
        ).status_code == 409
        async with app.router.lifespan_context(app):
            result = await wait_for(client, path + f"/evaluation-runs/{queued['id']}")
            assert result["gate"] == "pass", result
            result = await wait_for(client, path + f"/evaluation-runs/{live_run['id']}")
            assert result["status"] == "failed" and result["gate"] == "error"
            assert (await wait_for(client, path + f"/chat-sessions/{chat['id']}"))["status"] == "completed"
            # History export does not need the removed live credential binding.
            response = await client.get(
                path + f"/dataset-releases/{release['id']}/export",
                params={"agent_revision_id": live["id"], "mode": "live"},
            )
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert "OPAQUE" not in archive.read(".env.example").decode()


async def test_new_judge_uses_one_explicit_configuration_snapshot(client, app, case_payload, monkeypatch):
    object.__setattr__(app.state.settings, "allow_live_judge", True)
    monkeypatch.setattr("goldenloop_api.judging.importlib.util.find_spec", lambda name: object())
    for key, value in {
        "ENDPOINT": "https://judge.example.com",
        "DEPLOYMENT": "original",
        "API_VERSION": "v1",
        "API_KEY": "original-key",
    }.items():
        monkeypatch.setenv("GOLDENLOOP_JUDGE_" + key, value)
    calls, closed = [], []

    def factory(**snapshot):
        calls.append(snapshot)
        assert snapshot == {
            "endpoint": "https://judge.example.com",
            "deployment": "original",
            "api_version": "v1",
            "api_key": "original-key",
        }
        monkeypatch.setenv("GOLDENLOOP_JUDGE_DEPLOYMENT", "changed")
        monkeypatch.setenv("GOLDENLOOP_JUDGE_API_KEY", "rotated")
        transport = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(
                        model="observed",
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                message=SimpleNamespace(
                                    refusal=None,
                                    content=json.dumps(
                                        {
                                            "score": 1.0,
                                            "reason": "Correct",
                                            "evidence": ["C-123"],
                                            "insufficient_evidence": False,
                                        }
                                    ),
                                ),
                            )
                        ],
                    )
                )
            ),
            close=lambda: closed.append(True),
        )
        return OpenAIJudge(transport, model=snapshot["deployment"], provider="azure-openai")

    monkeypatch.setattr("goldenloop_api.judging.OpenAIJudge.from_azure", factory)
    path, _agent, revision = await setup_project(client)
    case_payload["checks"].append({"kind": "judge", "config": {"rubric": "Correct", "threshold": 0.5}})
    cases = []
    for _ in range(2):
        case, _release = await publish_v2(client, path, case_payload)
        cases.append(case)
    release = (
        await client.post(
            path + "/dataset-releases",
            json={
                "name": "Both",
                "case_ids": [c["id"] for c in cases],
                "expected_revisions": {c["id"]: 1 for c in cases},
            },
        )
    ).json()
    response = await client.post(
        path + "/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision_id": revision["id"],
            "mode": "mock",
            "judge": "azure",
            "idempotency_key": "judge-snapshot",
        },
    )
    assert response.status_code == 202, response.text
    result = await wait_for(client, path + "/evaluation-runs/" + response.json()["id"])
    assert result["gate"] == "pass", result
    assert len(calls) == len(closed) == 2
    assert "original-key" not in json.dumps(result)


async def test_openapi_shared_project_parameters_and_v1_literals(client, app):
    schema = app.openapi()
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch"}:
                continue
            if "{project_id}" in path:
                assert any(
                    p["name"] == "project_id" and p["in"] == "path" and p["required"]
                    for p in operation["parameters"]
                ), path
    assert schema["components"]["schemas"]["RunRecord"]["properties"]["agent_revision"]["enum"] == [
        "fixed",
        "buggy",
    ]
    assert schema["components"]["schemas"]["RunRecordV2"]["properties"]["agent_revision"]["type"] == "string"
    response = await client.get(DEMO + "/summary")
    assert response.status_code == 200


async def test_buggy_fixed_pins_survive_metadata_and_new_revision(client, case_payload):
    path, agent, fixed = await setup_project(client)
    _, release = await publish_v2(client, path, case_payload)
    revision_path = path + f"/agents/{agent['id']}/revisions"
    buggy = (await client.post(revision_path, json={"label": "Buggy", "spec": {"variant": "buggy"}})).json()
    for revision, gate in ((buggy, "fail"), (fixed, "pass")):
        response = await client.post(
            path + "/evaluation-runs",
            json={
                "release_id": release["id"],
                "agent_revision_id": revision["id"],
                "mode": "mock",
                "idempotency_key": gate,
            },
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        await client.post(revision_path, json={"label": "Later", "spec": {"variant": "fixed"}})
        await client.patch(path + f"/agents/{agent['id']}", json={"name": "Renamed"})
        result = await wait_for(client, path + f"/evaluation-runs/{run_id}")
        assert result["gate"] == gate and result["spec_hash"] == revision["spec_hash"]
        assert result["agent_revision_id"] == revision["id"]
        assert result["lineage"]["agent_spec_hash"] == revision["spec_hash"]
