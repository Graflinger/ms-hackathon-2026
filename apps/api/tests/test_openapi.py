import json
import subprocess
import sys

import fastapi.routing
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from jsonschema import Draft202012Validator

from goldenloop_api.main import create_app

from .conftest import wait_for


def test_every_json_operation_has_a_typed_success_response():
    app = create_app()
    schema = app.openapi()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            response = schema["paths"][route.path][method.lower()]["responses"][str(route.status_code or 200)]
            if route.path.endswith(("/export", "/events")):
                assert route.response_model is None
                media = "application/zip" if route.path.endswith("/export") else "text/event-stream"
                assert set(response["content"]) == {media}
            else:
                assert route.response_model is not None, route.path
                body = response["content"]["application/json"]["schema"]
                assert "$ref" in body or "$ref" in body.get("items", {}), route.path


def test_schema_export_is_deterministic_and_does_not_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENLOOP_DATA_DIR", str(tmp_path / "not-created"))
    first = subprocess.check_output([sys.executable, "-m", "goldenloop_api.openapi"], text=True)
    second = subprocess.check_output([sys.executable, "-m", "goldenloop_api.openapi"], text=True)
    assert first == second
    assert json.loads(first) == create_app().openapi()
    assert not (tmp_path / "not-created").exists()


async def test_all_json_endpoints_preserve_wire_shapes(client, app, case_payload, monkeypatch):
    original = fastapi.routing.serialize_response
    covered = set()
    schema = app.openapi()

    async def unchanged_response(*args, **kwargs):
        result = await original(*args, **kwargs)
        content = json.loads(result) if kwargs.get("dump_json") else result
        assert content == jsonable_encoder(kwargs["response_content"])
        return result

    monkeypatch.setattr(fastapi.routing, "serialize_response", unchanged_response)

    async def call(method, path, **kwargs):
        response = await client.request(method, "/api/v1" + path, **kwargs)
        assert response.is_success, response.text
        body = response.json()
        for route in app.routes:
            if (
                isinstance(route, APIRoute)
                and method in route.methods
                and route.path_regex.fullmatch("/api/v1" + path)
            ):
                covered.add((method, route.path))
                output = schema["paths"][route.path][method.lower()]["responses"][str(response.status_code)]
                validator = Draft202012Validator(
                    {
                        **output["content"]["application/json"]["schema"],
                        "components": schema["components"],
                    }
                )
                validator.validate(body)
                break
        return body

    await call("GET", "/health")
    await call("GET", "/summary")
    created = await call("POST", "/cases", json={"case": case_payload})
    assert created["reviewer"] is None and created["reason"] is None
    case = created["case"]
    case_path = "/cases/" + case["id"]
    await call("GET", case_path)
    await call("GET", "/cases")
    edited = await call("PUT", case_path, json={"case": case, "expected_revision": 1, "reason": "Edit"})
    await call("POST", case_path + "/approve", json={"revision": 2, "reason": "Reviewed"})
    release = await call(
        "POST",
        "/dataset-releases",
        json={
            "name": "Contract test",
            "case_ids": [case["id"]],
            "expected_revisions": {case["id"]: edited["case"]["revision"]},
        },
    )
    await call("GET", "/dataset-releases")
    await call("GET", "/dataset-releases/" + release["id"])
    preview = await call(
        "POST", "/imports/preview", files={"file": ("cases.csv", b"question\nLook up C-123\n", "text/csv")}
    )
    await call(
        "POST",
        f"/imports/{preview['id']}/commit",
        json={"mapping": {"user": "question"}, "duplicate_policy": "new"},
    )
    chat = await call("POST", "/chat-sessions", json={"agent_revision": "fixed"})
    assert chat["error"] is None and chat["messages"] == [] and chat["tool_calls"] == []
    chat_path = "/chat-sessions/" + chat["id"]
    chats = await call("GET", "/chat-sessions")
    assert "messages" not in chats[0] and "tool_calls" not in chats[0]
    await call("POST", chat_path + "/messages", json={"content": "Look up C-123"})
    await wait_for(client, "/api/v1" + chat_path)
    await call("GET", chat_path)
    feedback = await call(
        "POST",
        "/feedback",
        json={
            "session_id": chat["id"],
            "turn": 0,
            "target": "answer",
            "issue_type": "content",
            "comment": "Review this answer",
        },
    )
    assert all(feedback[key] is None for key in ("tool_call_id", "correction", "reason", "candidate_id"))
    await call(
        "POST", f"/feedback/{feedback['id']}/review", json={"status": "accepted", "reason": "Reviewed"}
    )
    await call("POST", f"/feedback/{feedback['id']}/candidate")
    await call("GET", "/feedback")
    run = await call(
        "POST",
        "/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision": "fixed",
            "idempotency_key": "contract",
        },
    )
    assert run["gate"] is None and "results" not in run
    run_path = "/evaluation-runs/" + run["id"]
    await wait_for(client, "/api/v1" + run_path)
    detail = await call("GET", run_path)
    assert detail["results"] and detail["error"] is None
    await call("GET", "/evaluation-runs")
    cancelled = await call("POST", run_path + "/cancel")
    assert "results" in cancelled and "lineage" in cancelled
    expected = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.response_model is not None
        for method in route.methods
    }
    assert covered == expected
