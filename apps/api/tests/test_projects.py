import asyncio
import io
import json
import zipfile

from goldenloop_eval.bundles import run_bundle

from .conftest import wait_for

DEMO = "/api/v2/projects/synthetic-demo"


async def setup_project(client, name="Other"):
    response = await client.post("/api/v2/projects", json={"name": name})
    assert response.status_code == 201, response.text
    project = response.json()
    path = "/api/v2/projects/" + project["id"]
    response = await client.post(path + "/agents", json={"name": "Customer agent"})
    assert response.status_code == 201, response.text
    agent = response.json()
    response = await client.post(
        path + f"/agents/{agent['id']}/revisions", json={"label": "Fixed", "spec": {"variant": "fixed"}}
    )
    assert response.status_code == 201, response.text
    return path, agent, response.json()


async def publish_v2(client, path, payload):
    response = await client.post(path + "/cases", json={"case": payload})
    assert response.status_code == 201, response.text
    case = response.json()["case"]
    assert "project_id" not in case
    response = await client.post(
        path + f"/cases/{case['id']}/approve", json={"revision": 1, "reason": "Reviewed"}
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        path + "/dataset-releases",
        json={"name": "Release", "case_ids": [case["id"]], "expected_revisions": {case["id"]: 1}},
    )
    assert response.status_code == 201, response.text
    return case, response.json()


async def test_v2_end_to_end_isolation_and_export(client, case_payload, tmp_path):
    path, agent, revision = await setup_project(client)
    case, release = await publish_v2(client, path, case_payload)
    assert (await client.get(DEMO + "/cases")).json() == []
    assert (await client.get("/api/v1/cases")).json() == []
    assert (await client.get(path + "/summary")).json() == {
        "candidates": 0,
        "releases": 1,
        "runs": 0,
        "feedback": 0,
    }
    for prefix in (DEMO, "/api/v1"):
        assert (await client.get(prefix + f"/cases/{case['id']}")).status_code == 404
        assert (await client.get(prefix + f"/dataset-releases/{release['id']}")).status_code == 404
    body = {
        "release_id": release["id"],
        "agent_revision_id": revision["id"],
        "mode": "mock",
        "idempotency_key": "same",
    }
    response = await client.post(path + "/evaluation-runs", json=body)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    result = await wait_for(client, path + f"/evaluation-runs/{run_id}")
    assert result["gate"] == "pass", result
    assert result["agent_id"] == agent["id"] and result["spec_hash"] == revision["spec_hash"]
    assert result["results"][0]["observation"]["agent_revision"] == revision["id"]
    assert (await client.post(path + "/evaluation-runs", json=body)).json()["id"] == run_id
    assert (await client.post(path + "/evaluation-runs", json={**body, "mode": "live"})).status_code == 409
    for prefix in (DEMO, "/api/v1"):
        assert (await client.get(prefix + "/evaluation-runs")).json() == []
        for suffix in ("", "/events"):
            assert (await client.get(prefix + f"/evaluation-runs/{run_id}" + suffix)).status_code == 404
        assert (await client.post(prefix + f"/evaluation-runs/{run_id}/cancel")).status_code == 404
    response = await client.get(path + f"/dataset-releases/{release['id']}/export")
    assert response.status_code == 422
    query = {"agent_revision_id": revision["id"], "mode": "mock"}
    response = await client.get(path + f"/dataset-releases/{release['id']}/export", params=query)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == "2" and manifest["agent_spec_hash"] == revision["spec_hash"]
        archive.extractall(tmp_path / "bundle")
    assert (await run_bundle(tmp_path / "bundle"))["gate"] == "pass"
    assert (
        await client.get(DEMO + f"/dataset-releases/{release['id']}/export", params=query)
    ).status_code == 404
    assert (
        await client.get(
            path + f"/dataset-releases/{release['id']}/export",
            params={**query, "agent_revision_id": "synthetic-fixed"},
        )
    ).status_code == 404


async def test_chat_feedback_indirect_scopes_and_archive(client, case_payload):
    path, agent, revision = await setup_project(client)
    response = await client.post(path + "/chat-sessions", json={"agent_revision_id": revision["id"]})
    assert response.status_code == 201, response.text
    chat_id = response.json()["id"]
    response = await client.post(
        path + f"/chat-sessions/{chat_id}/messages", json={"content": "Look up C-123"}
    )
    assert response.status_code == 202, response.text
    await wait_for(client, path + f"/chat-sessions/{chat_id}")
    feedback_body = {
        "session_id": chat_id,
        "turn": 0,
        "target": "answer",
        "issue_type": "content",
        "comment": "Review",
    }
    response = await client.post(path + "/feedback", json=feedback_body)
    assert response.status_code == 201, response.text
    feedback_id = response.json()["id"]
    for prefix in (DEMO, "/api/v1"):
        assert (await client.get(prefix + "/chat-sessions")).json() == []
        assert (await client.get(prefix + "/feedback")).json() == []
        assert (await client.post(prefix + "/feedback", json=feedback_body)).status_code == 404
        for suffix in ("", "/events"):
            assert (await client.get(prefix + f"/chat-sessions/{chat_id}" + suffix)).status_code == 404
        assert (await client.post(prefix + f"/feedback/{feedback_id}/candidate")).status_code == 404
        assert (
            await client.post(
                prefix + f"/feedback/{feedback_id}/review", json={"status": "accepted", "reason": "Review"}
            )
        ).status_code == 404
    assert (await client.post(path + f"/feedback/{feedback_id}/candidate")).status_code == 200
    assert (await client.get(path + "/summary")).json()["feedback"] == 1
    assert (await client.get(path + f"/chat-sessions/{chat_id}/events")).status_code == 200
    await client.patch(path + f"/agents/{agent['id']}", json={"archived": True})
    assert (
        await client.post(path + f"/chat-sessions/{chat_id}/messages", json={"content": "Again"})
    ).status_code == 409
    assert (await client.post(path + f"/feedback/{feedback_id}/candidate")).status_code == 409
    await client.patch(path + f"/agents/{agent['id']}", json={"archived": False})
    _, release = await publish_v2(client, path, case_payload)
    await client.patch(path, json={"archived": True})
    assert (await client.post(path + "/cases", json={"case": case_payload})).status_code == 409
    assert (await client.get(path + "/cases")).status_code == 200
    assert (
        await client.get(
            path + f"/dataset-releases/{release['id']}/export",
            params={"agent_revision_id": revision["id"], "mode": "mock"},
        )
    ).status_code == 200
    assert (await client.patch(path, json={"archived": False})).status_code == 200


async def test_registry_numbers_bindings_capabilities_and_legacy_visibility(
    client, case_payload, monkeypatch
):
    path, agent, _revision = await setup_project(client)
    revisions_path = path + f"/agents/{agent['id']}/revisions"
    results = await asyncio.gather(
        *[client.post(revisions_path, json={"label": "Next", "spec": {"variant": "buggy"}}) for _ in range(5)]
    )
    assert all(r.status_code == 201 for r in results)
    assert sorted(r.json()["number"] for r in results) == [2, 3, 4, 5, 6]
    assert (await client.get(DEMO + f"/agents/{agent['id']}")).status_code == 404
    assert (await client.get(DEMO + f"/agents/{agent['id']}/revisions")).status_code == 404
    assert (await client.get(revisions_path + "/synthetic-fixed")).status_code == 404
    project_id = path.rsplit("/", 1)[-1]
    bindings = {
        "approved": {
            "endpoint": "https://approved.example.com",
            "auth": "api_key",
            "key_env": "PRIVATE_KEY_NAME",
            "projects": [project_id],
        },
        "foreign": {
            "endpoint": "https://other.example.com",
            "auth": "azure_cli",
            "projects": ["synthetic-demo"],
        },
    }
    monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", json.dumps(bindings))
    monkeypatch.delenv("PRIVATE_KEY_NAME", raising=False)
    response = await client.get(path + "/connection-bindings")
    assert response.json() == [
        {"id": "approved", "endpoint": "https://approved.example.com", "auth": "api_key", "configured": False}
    ]
    connection = {
        "endpoint": "https://approved.example.com",
        "auth": "api_key",
        "binding": "approved",
        "deployment": "model",
        "api_version": "v1",
    }
    live = {"variant": "fixed", "modes": ["live"], "connection": connection}
    assert (await client.post(revisions_path, json={"label": "Live", "spec": live})).status_code == 201
    assert (
        await client.post(
            revisions_path,
            json={
                "label": "Bad",
                "spec": {**live, "connection": {**connection, "endpoint": "https://evil.example.com"}},
            },
        )
    ).status_code == 409
    response = await client.post(
        revisions_path,
        json={"label": "No history", "spec": {"variant": "fixed", "supports_multi_turn": False}},
    )
    narrow = response.json()
    _, release = await publish_v2(client, path, case_payload)
    response = await client.post(
        path + "/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision_id": narrow["id"],
            "mode": "mock",
            "idempotency_key": "incompatible",
        },
    )
    assert response.status_code == 409 and "multi-turn" in response.text
    response = await client.post(DEMO + "/chat-sessions", json={"agent_revision_id": "synthetic-fixed"})
    assert response.status_code == 201, response.text
    assert (await client.get("/api/v1/chat-sessions/" + response.json()["id"])).status_code == 404
    legacy = await client.post("/api/v1/chat-sessions", json={"agent_revision": "fixed"})
    assert legacy.status_code == 201
    assert len((await client.get("/api/v1/chat-sessions")).json()) == 1
    assert len((await client.get(DEMO + "/chat-sessions")).json()) == 2


async def test_imports_duplicates_and_mixed_mutations(client, case_payload):
    path, _agent, revision = await setup_project(client)
    other, _other_agent, other_revision = await setup_project(client, "Another")
    case, release = await publish_v2(client, path, case_payload)
    for method, suffix, body in (
        ("PUT", f"/cases/{case['id']}", {"case": case, "expected_revision": 1, "reason": "Edit"}),
        ("POST", f"/cases/{case['id']}/approve", {"revision": 1, "reason": "Review"}),
        (
            "POST",
            "/dataset-releases",
            {"name": "Mixed", "case_ids": [case["id"]], "expected_revisions": {case["id"]: 1}},
        ),
        (
            "POST",
            "/evaluation-runs",
            {
                "release_id": release["id"],
                "agent_revision_id": other_revision["id"],
                "mode": "mock",
                "idempotency_key": "mixed",
            },
        ),
        ("POST", "/chat-sessions", {"agent_revision_id": revision["id"]}),
    ):
        response = await client.request(method, other + suffix, json=body)
        assert response.status_code == 404, response.text
    for prefix in (path, other):
        response = await client.post(
            prefix + "/imports/preview",
            files={"file": ("cases.csv", b"id,question\nexternal,Look up C-999\n", "text/csv")},
        )
        assert response.status_code == 200, response.text
        import_id = response.json()["id"]
        body = {"mapping": {"id": "id", "user": "question"}, "duplicate_policy": "new"}
        foreign = other if prefix == path else path
        assert (await client.post(foreign + f"/imports/{import_id}/commit", json=body)).status_code == 404
        response = await client.post(prefix + f"/imports/{import_id}/commit", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["cases"][0]["case"]["source"]["uploaded_case_id"] == "external"
    fresh, _agent, _revision = await setup_project(client, "Fresh")
    for expected in (200, 409):
        response = await client.post(
            fresh + "/imports/preview",
            files={"file": ("cases.csv", b"question\nLook up C-999\n", "text/csv")},
        )
        result = await client.post(
            fresh + f"/imports/{response.json()['id']}/commit",
            json={"mapping": {"user": "question"}, "duplicate_policy": "reject"},
        )
        assert result.status_code == expected, result.text
