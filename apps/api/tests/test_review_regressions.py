import asyncio
import json
from contextlib import asynccontextmanager

import aiosqlite
import httpx
import pytest
from sqlalchemy import text

from goldenloop_api.bootstrap import migrate
from goldenloop_api.db import CaseHead, Project, Run
from goldenloop_api.main import create_app

from .conftest import publish, wait_for
from .test_project_migration import old_database
from .test_projects import DEMO, publish_v2, setup_project


@pytest.fixture
async def queued_app(settings):
    await asyncio.to_thread(migrate, settings)
    app = create_app(settings)
    try:
        yield app
    finally:
        await app.state.db.engine.dispose()


@pytest.fixture
async def queued_client(queued_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=queued_app), base_url="http://localhost"
    ) as client:
        yield client


async def test_case_named_cancel_cannot_bypass_archive(queued_client, case_payload):
    client = queued_client
    case, release = await publish(client, {**case_payload, "id": "cancel"})
    run = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={
                "release_id": release["id"],
                "agent_revision": "fixed",
                "idempotency_key": "cancel-case",
            },
        )
    ).json()
    await client.patch(DEMO, json={"archived": True})
    for prefix in ("/api/v1", DEMO):
        response = await client.put(
            prefix + "/cases/cancel",
            json={
                "case": {**case, "title": "Forbidden edit"},
                "expected_revision": 1,
                "reason": "Edit",
            },
        )
        assert response.status_code == 409, response.text
        assert (await client.get(prefix + "/cases/cancel")).json()["case"] == case
        response = await client.post(prefix + f"/evaluation-runs/{run['id']}/cancel")
        assert response.status_code == 200 and response.json()["status"] == "cancelled"


async def test_archive_is_rechecked_after_waiting_for_writer(
    queued_app, queued_client, case_payload, monkeypatch
):
    client, db = queued_client, queued_app.state.db
    case, _release = await publish(client, {**case_payload, "id": "cancel"})
    waiting = asyncio.Event()
    original = db.write

    @asynccontextmanager
    async def observed_write():
        waiting.set()
        async with original() as session:
            yield session

    monkeypatch.setattr(db, "write", observed_write)
    await db.lock.acquire()
    request = asyncio.create_task(
        client.put(
            DEMO + "/cases/cancel",
            json={
                "case": case,
                "expected_revision": 1,
                "reason": "Edit",
            },
        )
    )
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        # Simulate the writer that archives the project while this request is waiting.
        async with db.sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            project = await session.get(Project, "synthetic-demo")
            project.archived = True
    finally:
        db.lock.release()
    response = await asyncio.wait_for(request, 2)
    assert response.status_code == 409
    async with db.sessions() as session:
        assert (await session.get(CaseHead, "cancel")).latest == 1


async def test_archived_run_retries_are_reads_not_new_submissions(queued_client, case_payload):
    client = queued_client
    path, agent, revision = await setup_project(client)
    _, release = await publish_v2(client, path, case_payload)
    body = {
        "release_id": release["id"],
        "agent_revision_id": revision["id"],
        "mode": "mock",
        "idempotency_key": "accepted",
    }
    run = (await client.post(path + "/evaluation-runs", json=body)).json()
    await client.patch(path + f"/agents/{agent['id']}", json={"archived": True})
    for archived_project in (False, True):
        if archived_project:
            await client.patch(path, json={"archived": True})
        response = await client.post(path + "/evaluation-runs", json=body)
        assert response.status_code == 202 and response.json()["id"] == run["id"]
        assert (
            await client.post(path + "/evaluation-runs", json={**body, "mode": "live"})
        ).status_code == 409
        assert (
            await client.post(path + "/evaluation-runs", json={**body, "idempotency_key": "new"})
        ).status_code == 409
        assert (await client.post(DEMO + "/evaluation-runs", json=body)).status_code == 404
        assert (await client.get(path + "/summary")).json()["runs"] == 1
    # The compatibility surface has the same archive-safe idempotency behavior.
    _, release = await publish(client, case_payload)
    legacy_body = {"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "accepted"}
    legacy = (await client.post("/api/v1/evaluation-runs", json=legacy_body)).json()
    await client.patch(DEMO, json={"archived": True})
    response = await client.post("/api/v1/evaluation-runs", json=legacy_body)
    assert response.status_code == 202 and response.json()["id"] == legacy["id"]
    assert (
        await client.post("/api/v1/evaluation-runs", json={**legacy_body, "idempotency_key": "new"})
    ).status_code == 409


@pytest.mark.parametrize("first_legacy", [False, True])
async def test_queue_capacity_is_global_for_runs_and_messages(
    queued_client, queued_app, case_payload, first_legacy
):
    client = queued_client
    object.__setattr__(queued_app.state.settings, "max_pending", 1)
    path, _agent, revision = await setup_project(client)
    _, other_release = await publish_v2(client, path, case_payload)
    _, demo_release = await publish(client, case_payload)
    demo_prefix = "/api/v1" if first_legacy else DEMO
    demo_selector = {"agent_revision": "fixed"} if first_legacy else {"agent_revision_id": "synthetic-fixed"}
    response = await client.post(
        demo_prefix + "/evaluation-runs",
        json={"release_id": demo_release["id"], **demo_selector, "mode": "mock", "idempotency_key": "first"},
    )
    assert response.status_code == 202, response.text
    response = await client.post(
        path + "/evaluation-runs",
        json={
            "release_id": other_release["id"],
            "agent_revision_id": revision["id"],
            "mode": "mock",
            "idempotency_key": "second",
        },
    )
    assert response.status_code == 429, response.text
    demo_chat = (await client.post(demo_prefix + "/chat-sessions", json=demo_selector)).json()
    other_chat = (
        await client.post(path + "/chat-sessions", json={"agent_revision_id": revision["id"]})
    ).json()
    assert (
        await client.post(
            demo_prefix + f"/chat-sessions/{demo_chat['id']}/messages", json={"content": "C-123"}
        )
    ).status_code == 202
    assert (
        await client.post(path + f"/chat-sessions/{other_chat['id']}/messages", json={"content": "C-123"})
    ).status_code == 429


@pytest.mark.parametrize("mode", ["mock", "live"])
async def test_migrated_evidence_is_unverified_and_old_queue_never_invokes(settings, monkeypatch, mode):
    engine = old_database(settings)
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE evaluation_runs SET mode=:mode"), {"mode": mode})
            connection.execute(
                text("UPDATE evaluation_runs SET lineage=:lineage"),
                {
                    "lineage": json.dumps(
                        {"sdk_version": "0.1.0", "agent_package_version": "0.1.0", "agent_revision": "fixed"}
                    )
                },
            )
            before = connection.execute(
                text("SELECT id, lineage, results FROM evaluation_runs ORDER BY id")
            ).all()
        await asyncio.to_thread(migrate, settings)
        app = create_app(settings)
        invoked = []

        async def forbidden(*args, **kwargs):
            invoked.append(True)
            raise AssertionError("Historical incompatible work must never invoke")

        monkeypatch.setattr("goldenloop_api.runner.run_case", forbidden)
        monkeypatch.setattr("goldenloop_api.runner.run_revision", forbidden)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            ) as client:
                runs = (await client.get(DEMO + "/evaluation-runs")).json()
                assert len(runs) == 3
                assert all(row["spec_hash"] is None and row["legacy"] for row in runs)
                queued = next(row for row in runs if row["id"] == "old-run-1")
                assert queued["status"] == "interrupted" and queued["gate"] == "error"
                revision = (
                    await client.get(DEMO + "/agents/synthetic-customer-lookup/revisions/synthetic-fixed")
                ).json()
                assert revision["legacy"] and revision["spec_provenance"] == "mapping_only"
            async with app.state.db.sessions() as session:
                rows = (
                    await session.execute(
                        text("SELECT id, lineage, results FROM evaluation_runs ORDER BY id")
                    )
                ).all()
                assert rows == before
            assert not invoked
    finally:
        engine.dispose()


async def test_new_v1_versions_and_v2_mapping_execution_are_truthful(client, case_payload):
    _, release = await publish(client, case_payload)
    legacy = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "current-v1"},
        )
    ).json()
    result = await wait_for(client, "/api/v1/evaluation-runs/" + legacy["id"])
    assert result["gate"] == "pass"
    assert result["lineage"]["sdk_version"] == result["lineage"]["agent_package_version"] == "0.2.0"
    revision = (await client.get(DEMO + "/agents/synthetic-customer-lookup/revisions/synthetic-fixed")).json()
    response = await client.post(
        DEMO + "/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision_id": revision["id"],
            "mode": "mock",
            "idempotency_key": "current-v2",
        },
    )
    result = await wait_for(client, DEMO + "/evaluation-runs/" + response.json()["id"])
    assert result["gate"] == "pass" and result["legacy"] is False
    assert result["spec_hash"] == revision["spec_hash"] == result["lineage"]["agent_spec_hash"]


@pytest.mark.parametrize("phase", ["project", "record", "batch"])
async def test_cancel_at_sqlite_cursor_boundary_closes_reader(
    queued_app, queued_client, case_payload, monkeypatch, phase
):
    client, db = queued_client, queued_app.state.db
    _, release = await publish(client, case_payload)
    run = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "disconnect"},
        )
    ).json()
    entered, finish = asyncio.Event(), asyncio.Event()
    original = aiosqlite.Connection._execute
    needles = {"project": "FROM projects", "record": "FROM evaluation_runs", "batch": "FROM events"}

    async def blocked(connection, fn, *args, **kwargs):
        result = await original(connection, fn, *args, **kwargs)
        if (
            not entered.is_set()
            and getattr(fn, "__name__", "") == "execute"
            and args
            and needles[phase] in str(args[0])
        ):
            # Cursor exists and may hold a SQLite read lock, but has not reached close().
            entered.set()
            await finish.wait()
        return result

    monkeypatch.setattr(aiosqlite.Connection, "_execute", blocked)
    request = asyncio.create_task(client.get(f"/api/v1/evaluation-runs/{run['id']}/events"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        request.cancel()
        await asyncio.sleep(0)
        request.cancel()  # Repeated cancellation must not cancel the protected read task.
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(request, 2)
    assert db.engine.pool.checkedout() == 0
    async with asyncio.timeout(2), db.write() as session:
        row = await session.get(Run, run["id"])
        row.status, row.gate = "cancelled", "error"
    assert db.engine.pool.checkedout() == 0


async def test_asgi_network_disconnect_closes_stream_before_next_write(
    queued_app, queued_client, case_payload
):
    client, db = queued_client, queued_app.state.db
    _, release = await publish(client, case_payload)
    run = (
        await client.post(
            "/api/v1/evaluation-runs",
            json={"release_id": release["id"], "agent_revision": "fixed", "idempotency_key": "network"},
        )
    ).json()
    messages = asyncio.Queue()
    await messages.put({"type": "http.request", "body": b"", "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            # No session/transaction may survive across an SSE yield.
            assert db.engine.pool.checkedout() == 0
            await messages.put({"type": "http.disconnect"})

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "query_string": b"",
        "path": f"/api/v1/evaluation-runs/{run['id']}/events",
        "root_path": "",
        "headers": [(b"host", b"localhost")],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
    }
    await asyncio.wait_for(queued_app(scope, messages.get, send), 2)
    assert any(message["type"] == "http.response.body" for message in sent)
    assert db.engine.pool.checkedout() == 0
    async with asyncio.timeout(2), db.write() as session:
        row = await session.get(Run, run["id"])
        row.status = "running"
    await queued_app.state.runner.execute_run(run["id"])
    result = await wait_for(client, f"/api/v1/evaluation-runs/{run['id']}")
    assert result["gate"] == "pass"
