import asyncio
import io
import json
import os
import subprocess
import sys
import zipfile

import httpx
import pytest
from goldenloop_eval import Case, release_hash
from goldenloop_eval.cli import load_bundle
from sqlalchemy import func, select, text

from goldenloop_api.bootstrap import main as bootstrap_main
from goldenloop_api.bootstrap import migrate, seed
from goldenloop_api.config import Settings
from goldenloop_api.db import CaseHead, Database, Run, journal_mode
from goldenloop_api.main import create_app
from goldenloop_api.security import process_lock

from .conftest import publish, wait_for


async def test_health_summary_and_openapi(client):
    assert (await client.get("/api/v1/health")).json() == {
        "status": "ok",
        "mode": "local-synthetic",
        "sdk_version": "0.1.0",
    }
    assert (await client.get("/api/v1/summary")).json() == {
        "candidates": 0,
        "releases": 0,
        "runs": 0,
        "feedback": 0,
    }
    schema = (await client.get("/api/v1/openapi.json")).json()
    assert "/api/v1/evaluation-runs" in schema["paths"]


async def test_explicit_local_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDENLOOP_LOCAL_DEMO", raising=False)
    assert Settings().local_demo is False
    app = create_app(Settings(data_dir=tmp_path, local_demo=False))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client,
    ):
        assert (await client.get("/api/v1/health")).status_code == 403
    assert not (tmp_path / "goldenloop.db").exists()


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "example.com"},
        {"origin": "https://attacker.example"},
        {"origin": "null"},
        {"origin": "http://localhost.attacker.example"},
        {"sec-fetch-site": "cross-site"},
    ],
)
async def test_nonlocal_requests_rejected(client, headers):
    assert (await client.get("/api/v1/health", headers=headers)).status_code == 403


async def test_proxy_and_peer_boundaries(app, client):
    assert (
        await client.get(
            "/api/v1/health", headers={"origin": "http://localhost:5173", "host": "127.0.0.1:8000"}
        )
    ).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.2", 1234)), base_url="http://localhost"
    ) as remote:
        assert (await remote.get("/api/v1/health")).status_code == 403


async def test_bootstrap_required_and_idempotent(settings):
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="bootstrapped"):
        async with app.router.lifespan_context(app):
            pass
    await asyncio.to_thread(migrate, settings)
    await seed(settings)
    await asyncio.to_thread(migrate, settings)
    await seed(settings)
    db = Database(settings)
    try:
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(CaseHead)) == 1
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            assert await session.scalar(text("PRAGMA busy_timeout")) == 5000
            assert (await session.scalar(text("PRAGMA journal_mode"))).upper() == journal_mode()
            assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
    finally:
        await db.engine.dispose()


def test_cli_help_and_opt_in(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        bootstrap_main(["--help"])
    assert exc.value.code == 0
    assert "GOLDENLOOP_DATA_DIR" in capsys.readouterr().out
    monkeypatch.delenv("GOLDENLOOP_LOCAL_DEMO", raising=False)
    with pytest.raises(SystemExit) as exc:
        bootstrap_main([])
    assert exc.value.code == 2


def test_single_process_lock(tmp_path):
    with (
        process_lock(tmp_path / "lock"),
        pytest.raises(RuntimeError, match="Another API process"),
        process_lock(tmp_path / "lock"),
    ):
        pass
    with process_lock(tmp_path / "lock"):
        pass


async def test_cases_revisions_approval_release_immutability(client, case_payload):
    case, release = await publish(client, case_payload)
    original = (await client.get(f"/api/v1/dataset-releases/{release['id']}")).json()
    assert original["content_hash"] == release_hash([Case.model_validate(case)])
    updated = {**case, "title": "Edited candidate", "checks": []}
    edit = {"case": updated, "expected_revision": 1, "reason": "New expectations needed"}
    response = await client.put(f"/api/v1/cases/{case['id']}", json=edit)
    assert response.status_code == 200, response.text
    assert response.json()["case"]["revision"] == 2
    assert response.json()["status"] == "candidate"
    assert response.json()["reviewer"] is None
    assert (await client.put(f"/api/v1/cases/{case['id']}", json=edit)).status_code == 409
    assert (await client.get(f"/api/v1/dataset-releases/{release['id']}")).json() == original
    assert (
        await client.post(f"/api/v1/cases/{case['id']}/approve", json={"revision": 1, "reason": "stale"})
    ).status_code == 409
    assert (
        await client.post(f"/api/v1/cases/{case['id']}/approve", json={"revision": 2, "reason": "no checks"})
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/dataset-releases",
            json={"name": "unreviewed", "case_ids": [case["id"]], "expected_revisions": {case["id"]: 2}},
        )
    ).status_code == 409
    assert len((await client.get("/api/v1/cases")).json()) == 1
    assert (await client.get("/api/v1/summary")).json()["candidates"] == 1


async def test_case_errors_and_sanitization(client, case_payload, monkeypatch, app):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "opaque-credential-value")
    body = {
        **case_payload,
        "title": "Contact person@example.com opaque-credential-value",
        "source": {"api_key": "anything", "comment": "Bearer private-token"},
    }
    response = await client.post("/api/v1/cases", json={"case": body, "reason": "password=unsafe"})
    assert response.status_code == 201, response.text
    assert "person@example.com" not in response.text and "opaque-credential-value" not in response.text
    assert response.json()["case"]["source"]["api_key"] == "[REDACTED]"
    assert response.json()["reason"] == "[REDACTED]"
    case = response.json()["case"]
    assert (await client.post("/api/v1/cases", json={"case": case})).status_code == 409
    assert (await client.get("/api/v1/cases/absent")).status_code == 404
    assert (
        await client.post("/api/v1/cases", json={"case": {**case_payload, "revision": 2}})
    ).status_code == 422
    assert (
        await client.post("/api/v1/cases", json={"case": {**case_payload, "turns": []}})
    ).status_code == 422
    assert (
        await client.post("/api/v1/cases", json={"case": {**case_payload, "fixture_version": "real"}})
    ).status_code == 422
    response = await client.post(
        "/api/v1/cases", json={"case": {"title": "password=secret", "turns": "sk-1234567890"}}
    )
    assert (
        response.status_code == 422
        and "sk-1234567890" not in response.text
        and "password=secret" not in response.text
    )
    async with app.state.db.sessions() as session:
        persisted = str((await session.execute(text("SELECT payload, reason FROM case_revisions"))).all())
        assert "opaque-credential-value" not in persisted and "person@example.com" not in persisted


async def test_concurrent_edits_conflict(client, case_payload):
    case = (await client.post("/api/v1/cases", json={"case": case_payload})).json()["case"]
    responses = await asyncio.gather(
        *[
            client.put(
                f"/api/v1/cases/{case['id']}",
                json={"case": case, "expected_revision": 1, "reason": "Concurrent edit"},
            )
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]


async def test_mock_runs_and_idempotency(client, case_payload):
    _, release = await publish(client, case_payload)
    body = {"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "same-key"}
    submissions = await asyncio.gather(*[client.post("/api/v1/evaluation-runs", json=body) for _ in range(2)])
    assert all(response.status_code == 202 for response in submissions)
    run_id = submissions[0].json()["id"]
    assert submissions[1].json()["id"] == run_id
    run = await wait_for(client, f"/api/v1/evaluation-runs/{run_id}")
    assert run["status"] == "completed" and run["gate"] == "pass", run
    assert run["lineage"]["content_hash"] == release["content_hash"]
    assert run["results"][0]["observation"]["trace_complete"] is True
    assert (
        await client.post("/api/v1/evaluation-runs", json={**body, "agent_revision": "buggy"})
    ).status_code == 409
    buggy = (
        await client.post(
            "/api/v1/evaluation-runs", json={**body, "agent_revision": "buggy", "idempotency_key": "buggy"}
        )
    ).json()
    result = await wait_for(client, f"/api/v1/evaluation-runs/{buggy['id']}")
    assert result["status"] == "completed" and result["gate"] == "fail"
    assert len((await client.get("/api/v1/evaluation-runs")).json()) == 2


async def test_unconfigured_judge_and_live_fail_closed(client, case_payload):
    case_payload["checks"] = [{"kind": "judge", "config": {"rubric": "Correct customer", "threshold": 0.8}}]
    _, release = await publish(client, case_payload)
    body = {"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "judge"}
    assert (await client.post("/api/v1/evaluation-runs", json=body)).status_code == 409
    response = await client.post(
        "/api/v1/evaluation-runs", json={**body, "mode": "live", "idempotency_key": "live"}
    )
    assert response.status_code == 409


async def test_cancel_running_and_events(client, case_payload, monkeypatch):
    started = asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr("goldenloop_api.runner.run_case", slow)
    _, release = await publish(client, case_payload)
    run = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "cancel"},
        )
    ).json()
    await asyncio.wait_for(started.wait(), 2)
    path = f"/api/v1/evaluation-runs/{run['id']}"
    result = (await client.post(path + "/cancel")).json()
    assert result["status"] == "cancelled" and result["gate"] == "error"
    assert (await client.post(path + "/cancel")).json()["status"] == "cancelled"
    events = await client.get(path + "/events")
    assert events.status_code == 200 and "event: status" in events.text
    ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids))
    resumed = await client.get(path + "/events", headers={"Last-Event-ID": str(ids[-1])})
    assert resumed.text == ""
    assert (await client.get(path + "/events?after=bad")).status_code == 422


async def test_timeout_and_secret_exception(client, case_payload, monkeypatch, app):
    async def fail(*args, **kwargs):
        raise RuntimeError("Bearer top-secret-provider-detail")

    monkeypatch.setattr("goldenloop_api.runner.run_case", fail)
    _, release = await publish(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "failed"},
    )
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["status"] == "failed" and result["gate"] == "error"
    assert "top-secret-provider-detail" not in json.dumps(result)

    async def slow(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr("goldenloop_api.runner.run_case", slow)
    object.__setattr__(app.state.settings, "case_timeout", 0.02)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "timeout"},
    )
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["status"] == "failed" and result["gate"] == "error"


async def test_export_independent_pytest(client, case_payload, tmp_path):
    _, release = await publish(client, case_payload)
    response = await client.get(f"/api/v1/dataset-releases/{release['id']}/export")
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    bundle = tmp_path / "bundle"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {"cases.json", "manifest.json", "test_release.py", "adapter.py", ".env.example"} <= set(
            archive.namelist()
        )
        archive.extractall(bundle)
    manifest, cases = load_bundle(bundle)
    assert manifest.content_hash == release_hash(cases) == release["content_hash"]
    # An import blocker makes the subprocess prove that the exported wrapper does not import the backend.
    script = """
import importlib.abc, sys
class NoBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("goldenloop_api"):
            raise ImportError("API unavailable in exported test environment")
sys.meta_path.insert(0, NoBackend())
import pytest
raise SystemExit(pytest.main(["-q", "test_release.py"]))
"""
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("GOLDENLOOP", "AZURE_OPENAI"))
    }
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        cwd=bundle,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_chat_feedback_candidate_keeps_observations_separate(client):
    chat = (await client.post("/api/v1/chat-sessions", json={"agent_revision": "buggy"})).json()
    path = f"/api/v1/chat-sessions/{chat['id']}"
    command = await client.post(path + "/messages", json={"content": "Look up C-123"})
    assert command.status_code == 202
    chat = await wait_for(client, path)
    assert chat["trace_complete"] is True and chat["mode"] == "mock"
    assert chat["tool_calls"][0]["arguments"]["customer_id"] == "C-999"
    await client.post(path + "/messages", json={"content": "Repeat the ID"})
    chat = await wait_for(client, path)
    assert len(chat["messages"]) == 4
    feedback_body = {
        "session_id": chat["id"],
        "turn": 0,
        "tool_call_id": chat["tool_calls"][0]["id"],
        "target": "tool",
        "issue_type": "wrong_argument",
        "comment": "Incorrect customer ID",
        "correction": {"customer_id": "C-123"},
    }
    feedback = await client.post("/api/v1/feedback", json=feedback_body)
    assert feedback.status_code == 201, feedback.text
    feedback_id = feedback.json()["id"]
    assert feedback.json()["status"] == "unresolved"
    await client.post(
        f"/api/v1/feedback/{feedback_id}/review",
        json={"status": "accepted", "reason": "Verified synthetic ID"},
    )
    candidate = (await client.post(f"/api/v1/feedback/{feedback_id}/candidate")).json()
    assert candidate["status"] == "candidate"
    assert candidate["case"]["checks"] == []
    assert candidate["case"]["turns"] == [{"user": "Look up C-123", "reference_answer": None}]
    assert candidate["case"]["source"]["feedback_id"] == feedback_id
    assert (await client.post(f"/api/v1/feedback/{feedback_id}/candidate")).json() == candidate
    assert (await client.get(path)).json()["tool_calls"] == chat["tool_calls"]
    assert len((await client.get("/api/v1/feedback")).json()) == 1
    events = await client.get(path + "/events")
    assert "event: message" in events.text and "event: tool_call" in events.text
    assert "event: delta" not in events.text
    assert (await client.post("/api/v1/feedback", json={**feedback_body, "turn": 1})).status_code == 422
    assert (
        await client.post("/api/v1/feedback", json={**feedback_body, "target": "missing_tool"})
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/feedback", json={**feedback_body, "tool_call_id": None, "target": "missing_tool"}
        )
    ).status_code == 201


async def test_pending_chat_and_shutdown_reconciliation(settings, case_payload, monkeypatch):
    await asyncio.to_thread(migrate, settings)
    app = create_app(settings)
    started = asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr("goldenloop_api.runner.run_case", slow)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client,
    ):
        chat = (await client.post("/api/v1/chat-sessions", json={"agent_revision": "fixed"})).json()
        path = f"/api/v1/chat-sessions/{chat['id']}"
        assert (await client.post(path + "/messages", json={"content": "C-123"})).status_code == 202
        await asyncio.wait_for(started.wait(), 2)
        assert (await client.post(path + "/messages", json={"content": "C-999"})).status_code == 409
    restarted = create_app(settings)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://localhost"
        ) as client,
    ):
        chat = (await client.get(path)).json()
        assert chat["status"] == "interrupted" and chat["trace_complete"] is False
        assert (await client.post(path + "/messages", json={"content": "C-123"})).status_code == 409


async def test_startup_running_to_interrupted_and_queued_recovery(settings, case_payload):
    await asyncio.to_thread(migrate, settings)
    app = create_app(settings)
    # Submit without lifespan: durable commands stay queued and are claimed only after startup.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        _, release = await publish(client, case_payload)
        runs = []
        for key in ("interrupted", "queued", "cancelled"):
            response = await client.post(
                "/api/v1/evaluation-runs",
                json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": key},
            )
            runs.append(response.json())
        await client.post(f"/api/v1/evaluation-runs/{runs[2]['id']}/cancel")
    async with app.state.db.write() as session:
        run = await session.get(Run, runs[0]["id"])
        run.status = "running"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client,
    ):
        assert (await client.get(f"/api/v1/evaluation-runs/{runs[0]['id']}")).json()[
            "status"
        ] == "interrupted"
        queued = await wait_for(client, f"/api/v1/evaluation-runs/{runs[1]['id']}")
        assert queued["status"] == "completed" and queued["gate"] == "pass"
        assert (await client.get(f"/api/v1/evaluation-runs/{runs[2]['id']}")).json()["status"] == "cancelled"


async def test_request_limits(client, app):
    object.__setattr__(app.state.settings, "max_request_bytes", 100)
    assert (await client.post("/api/v1/cases", content=b"x" * 101)).status_code == 413

    async def chunks():
        yield b"x" * 60
        yield b"y" * 60

    assert (await client.post("/api/v1/cases", content=chunks())).status_code == 413
