import asyncio
import io
import json

import httpx
import pytest
from goldenloop_demo_agent import run_case as demo_run_case
from openpyxl import Workbook
from sqlalchemy.exc import IntegrityError

from goldenloop_api.bootstrap import migrate
from goldenloop_api.db import MessageCommand, journal_mode
from goldenloop_api.main import create_app

from .conftest import publish, wait_for


def test_journal_version_guard(monkeypatch):
    monkeypatch.setattr("goldenloop_api.db.sqlite3.sqlite_version_info", (3, 51, 2))
    assert journal_mode() == "DELETE"
    monkeypatch.setattr("goldenloop_api.db.sqlite3.sqlite_version_info", (3, 51, 3))
    assert journal_mode() == "WAL"


async def test_foreign_keys_enforced(app):
    with pytest.raises(IntegrityError):
        async with app.state.db.write() as session:
            session.add(MessageCommand(session_id="absent", turn=0, content="synthetic"))
            await session.flush()


async def test_migrations_match_orm(app):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from goldenloop_api.db import Base

    async with app.state.db.engine.connect() as connection:
        differences = await connection.run_sync(
            lambda conn: compare_metadata(MigrationContext.configure(conn), Base.metadata)
        )
    assert differences == []


async def test_cancel_before_claim_and_queue_capacity(settings, case_payload):
    await asyncio.to_thread(migrate, settings)
    object.__setattr__(settings, "max_pending", 1)
    app = create_app(settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            _, release = await publish(client, case_payload)
            body = {"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "first"}
            run = (await client.post("/api/v1/evaluation-runs", json=body)).json()
            assert (
                await client.post("/api/v1/evaluation-runs", json={**body, "idempotency_key": "second"})
            ).status_code == 429
            assert (await client.post(f"/api/v1/evaluation-runs/{run['id']}/cancel")).json()[
                "status"
            ] == "cancelled"
            await app.state.runner.execute_run(run["id"])
            assert (await client.get(f"/api/v1/evaluation-runs/{run['id']}")).json()["results"] == []
            chat = (await client.post("/api/v1/chat-sessions", json={"agent_revision": "fixed"})).json()
            second = (await client.post("/api/v1/chat-sessions", json={"agent_revision": "fixed"})).json()
            assert (
                await client.post(f"/api/v1/chat-sessions/{chat['id']}/messages", json={"content": "C-123"})
            ).status_code == 202
            assert (
                await client.post(f"/api/v1/chat-sessions/{second['id']}/messages", json={"content": "C-999"})
            ).status_code == 429
    finally:
        await app.state.db.engine.dispose()


async def test_redaction_policy_change_blocks_export_and_execution(client, case_payload, monkeypatch):
    case_payload["context"] = "opaque-future-credential"
    _, release = await publish(client, case_payload)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "opaque-future-credential")
    response = await client.get(f"/api/v1/dataset-releases/{release['id']}/export")
    assert response.status_code == 409
    run = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={
                "release_id": release["id"],
                "agent_revision": "fixed",
                "idempotency_key": "redaction-change",
            },
        )
    ).json()
    result = await wait_for(client, f"/api/v1/evaluation-runs/{run['id']}")
    assert result["status"] == "failed" and result["gate"] == "error"
    assert "opaque-future-credential" not in json.dumps(result)


@pytest.mark.parametrize("variant", ["lineage", "missing-trace", "oversized"])
async def test_invalid_observations_never_pass(client, case_payload, monkeypatch, variant):
    async def invalid(case, revision, mode):
        observation = await demo_run_case(case, revision=revision, mode=mode)
        if variant == "lineage":
            observation.agent_revision = "not-selected"
        elif variant == "missing-trace":
            observation.trace_complete = False
        else:
            observation.messages[0]["content"] = "x" * (1024 * 1024)
        return observation

    monkeypatch.setattr("goldenloop_api.runner.run_case", invalid)
    _, release = await publish(client, case_payload)
    response = await client.post(
        "/api/v1/evaluation-runs",
        json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": variant},
    )
    result = await wait_for(client, f"/api/v1/evaluation-runs/{response.json()['id']}")
    assert result["gate"] == "error"


async def test_chat_failure_has_recovery_event(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("private provider diagnostics")

    monkeypatch.setattr("goldenloop_api.runner.run_case", fail)
    chat = (await client.post("/api/v1/chat-sessions", json={"agent_revision": "fixed"})).json()
    path = f"/api/v1/chat-sessions/{chat['id']}"
    await client.post(path + "/messages", json={"content": "C-123"})
    result = await wait_for(client, path)
    assert result["status"] == "failed" and result["trace_complete"] is False
    events = await client.get(path + "/events")
    assert '"status": "failed"' in events.text and "private provider diagnostics" not in events.text


async def test_sse_live_completion_and_cursor(client, case_payload):
    _, release = await publish(client, case_payload)
    run = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "sse-live"},
        )
    ).json()
    response = await client.get(f"/api/v1/evaluation-runs/{run['id']}/events")
    assert "event: result" in response.text and '"status": "completed"' in response.text
    ids = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
    response = await client.get(f"/api/v1/evaluation-runs/{run['id']}/events?after={ids[0]}")
    assert f"id: {ids[0]}\n" not in response.text
    assert "event: result" in response.text


async def test_bootstrap_preserves_edits(client, settings, case_payload):
    case = (await client.post("/api/v1/cases", json={"case": case_payload})).json()
    await asyncio.to_thread(migrate, settings)
    assert (await client.get(f"/api/v1/cases/{case['case']['id']}")).json() == case


async def test_case_validation_limits(client, case_payload):
    for update in (
        {"turns": [{"user": "synthetic"}] * 51},
        {"context": "x" * 140000},
        {"id": "bad/path"},
        {"checks": [{"kind": "content_contains", "turn": 20, "config": {"value": "C-123"}}]},
    ):
        assert (
            await client.post("/api/v1/cases", json={"case": {**case_payload, **update}})
        ).status_code == 422


async def test_workbook_sensitive_sheet_names_rejected(client):
    workbook = Workbook()
    workbook.active.title = "password"
    workbook.active.append(["question"])
    workbook.active.append(["C-123"])
    output = io.BytesIO()
    workbook.save(output)
    response = await client.post("/api/v1/imports/preview", files={"file": ("case.xlsx", output.getvalue())})
    assert response.status_code == 422


async def test_live_configuration_change_does_not_execute(settings, case_payload, monkeypatch):
    await asyncio.to_thread(migrate, settings)
    object.__setattr__(settings, "allow_live", True)
    monkeypatch.setattr("goldenloop_api.main.importlib.util.find_spec", lambda name: object())
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_COMPLETION_MODEL", "synthetic-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        _, release = await publish(client, case_payload)
        response = await client.post(
            "/api/v1/evaluation-runs",
            json={
                "release_id": release["id"],
                "agent_revision": "fixed",
                "mode": "live",
                "idempotency_key": "live-config",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
    monkeypatch.setenv("AZURE_OPENAI_CHAT_COMPLETION_MODEL", "different-deployment")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client,
    ):
        result = await wait_for(client, f"/api/v1/evaluation-runs/{run_id}")
        assert result["status"] == "failed" and result["gate"] == "error"
