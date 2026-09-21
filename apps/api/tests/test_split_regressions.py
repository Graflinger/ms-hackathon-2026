import asyncio
import hashlib
import io
import json
import zipfile
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from goldenloop_api.bootstrap import migrate
from goldenloop_api.db import CaseRevision, ReleaseCase, Run
from goldenloop_api.main import create_app

from .conftest import publish
from .test_projects import DEMO
from .test_release_and_judge import azure_judge as _azure_judge

azure_judge = _azure_judge


@pytest.fixture
async def app(settings):
    # No lifespan: submissions stay queued until the test explicitly executes them.
    await asyncio.to_thread(migrate, settings)
    application = create_app(settings)
    try:
        yield application
    finally:
        await application.state.db.engine.dispose()


def run_body(release, **kwargs):
    return {
        "release_id": release["id"],
        "agent_revision_id": "synthetic-fixed",
        "mode": "mock",
        "idempotency_key": "split-regression",
        **kwargs,
    }


async def execute(app, run_id):
    async with app.state.db.write() as session:
        row = await session.get(Run, run_id)
        assert row.status == "queued"
        row.status = "running"
    await app.state.runner.execute_run(run_id)


def forbid_invocations(monkeypatch):
    agent = AsyncMock(side_effect=AssertionError("Agent must not be invoked"))
    judge = Mock(side_effect=AssertionError("Judge must not be constructed"))
    monkeypatch.setattr("goldenloop_api.runner.run_case", agent)
    monkeypatch.setattr("goldenloop_api.runner.run_revision", agent)
    monkeypatch.setattr("goldenloop_api.judging.OpenAIJudge.from_azure", judge)
    return agent, judge


async def publish_pair(client, case_payload, *, split=False):
    cases = []
    for index in range(2):
        case, _ = await publish(
            client,
            {
                **case_payload,
                "id": f"case-{index}",
                "tags": ["split:test"] if split and index else ["split:development"],
            },
        )
        cases.append(case)
    response = await client.post(
        DEMO + "/dataset-releases",
        json={
            "name": "Two cases",
            "case_ids": [case["id"] for case in cases],
            "expected_revisions": {case["id"]: 1 for case in cases},
        },
    )
    assert response.status_code == 201, response.text
    return cases, response.json()


@pytest.mark.parametrize("checks,accepted", [(3, False), (2, True)])
async def test_repeated_judge_budget_boundary(client, app, case_payload, azure_judge, checks, accepted):
    object.__setattr__(app.state.settings, "max_judge_calls", 20)
    case_payload["checks"] += [
        {"kind": "judge", "config": {"rubric": f"Synthetic criterion {index}", "threshold": 0.8}}
        for index in range(checks)
    ]
    _, release = await publish(client, case_payload)
    response = await client.post(
        DEMO + "/evaluation-runs", json=run_body(release, judge="azure", repetitions=10)
    )
    assert response.status_code == (202 if accepted else 422), response.text
    assert azure_judge["calls"] == []
    async with app.state.db.sessions() as session:
        runs = (await session.scalars(select(Run))).all()
        assert len(runs) == int(accepted)
    if accepted:
        await execute(app, response.json()["id"])
        result = (await client.get(DEMO + "/evaluation-runs/" + response.json()["id"])).json()
        assert result["status"] == "completed" and result["gate"] == "pass", result
        assert result["metrics"]["repetitions_completed"] == 10
        assert len(azure_judge["calls"]) == 20
    else:
        assert (await client.get(DEMO + "/evaluation-runs")).json() == []
        assert azure_judge["closed"] == 0


async def test_queued_judge_rechecks_repeated_budget_before_invocation(
    client, app, case_payload, azure_judge, monkeypatch
):
    object.__setattr__(app.state.settings, "max_judge_calls", 20)
    case_payload["checks"] += [
        {"kind": "judge", "config": {"rubric": f"Synthetic criterion {index}", "threshold": 0.8}}
        for index in range(2)
    ]
    _, release = await publish(client, case_payload)
    response = await client.post(
        DEMO + "/evaluation-runs", json=run_body(release, judge="azure", repetitions=10)
    )
    assert response.status_code == 202, response.text
    object.__setattr__(app.state.settings, "max_judge_calls", 19)
    agent, judge = forbid_invocations(monkeypatch)
    await execute(app, response.json()["id"])
    result = (await client.get(DEMO + "/evaluation-runs/" + response.json()["id"])).json()
    assert result["status"] == "failed" and result["gate"] == "error", result
    assert result["results"] == []
    agent.assert_not_called()
    judge.assert_not_called()
    assert azure_judge["calls"] == []


@pytest.mark.parametrize("case_index", [0, 1], ids=["selected", "unselected"])
@pytest.mark.parametrize("phase", ["submission", "execution"])
async def test_entire_release_integrity_checked_before_split_execution(
    client, app, case_payload, monkeypatch, case_index, phase
):
    cases, release = await publish_pair(client, case_payload, split=True)
    body = run_body(release, dataset_split="development")
    if phase == "execution":
        response = await client.post(DEMO + "/evaluation-runs", json=body)
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        detail = (await client.get(DEMO + "/evaluation-runs/" + run_id)).json()
        assert detail["lineage"]["case_ids"] == [cases[0]["id"]]
        assert detail["lineage"]["release_content_hash"] == release["content_hash"]
        assert detail["lineage"]["content_hash"] != release["content_hash"]
    async with app.state.db.write() as session:
        row = await session.get(ReleaseCase, (release["id"], cases[case_index]["id"]))
        row.payload = {**row.payload, "title": "Corrupted after publication"}
    agent, judge = forbid_invocations(monkeypatch)
    if phase == "submission":
        response = await client.post(DEMO + "/evaluation-runs", json=body)
        assert response.status_code == 409, response.text
        assert "hash mismatch" in response.json()["detail"]
        async with app.state.db.sessions() as session:
            assert (await session.scalars(select(Run))).all() == []
    else:
        await execute(app, run_id)
        result = (await client.get(DEMO + "/evaluation-runs/" + run_id)).json()
        assert result["status"] == "failed" and result["gate"] == "error", result
        assert result["results"] == []
    agent.assert_not_called()
    judge.assert_not_called()


async def test_queued_selection_hash_is_revalidated(client, app, case_payload, monkeypatch):
    _, release = await publish_pair(client, case_payload, split=True)
    response = await client.post(DEMO + "/evaluation-runs", json=run_body(release))
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    async with app.state.db.write() as session:
        row = await session.get(Run, run_id)
        row.lineage = {**row.lineage, "content_hash": "0" * 64}
    agent, judge = forbid_invocations(monkeypatch)
    await execute(app, run_id)
    result = (await client.get(DEMO + "/evaluation-runs/" + run_id)).json()
    assert result["status"] == "failed" and result["gate"] == "error", result
    assert result["results"] == []
    agent.assert_not_called()
    judge.assert_not_called()


@pytest.mark.parametrize("prefix", ["/api/v1", DEMO], ids=["v1", "historical-v2"])
async def test_partial_historical_run_metrics_use_release_count(client, app, case_payload, prefix):
    _, release = await publish_pair(client, case_payload)
    body = run_body(release)
    if prefix == "/api/v1":
        body.pop("agent_revision_id")
        body["agent_revision"] = "fixed"
    response = await client.post(prefix + "/evaluation-runs", json=body)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    path = prefix + "/evaluation-runs/" + run_id
    # Produce genuine result evidence, then model an older partially persisted run.
    await execute(app, run_id)
    completed = (await client.get(path)).json()
    assert completed["status"] == "completed" and completed["gate"] == "pass", completed
    assert len(completed["results"]) == 2
    results = [
        {key: value for key, value in item.items() if key != "repetition"} for item in completed["results"]
    ]
    async with app.state.db.write() as session:
        row = await session.get(Run, run_id)
        row.lineage = {
            key: value
            for key, value in row.lineage.items()
            if key not in {"case_ids", "repetitions", "dataset_split", "release_content_hash"}
        }
        row.results, row.status, row.gate = results[:1], "running", None
    running = (await client.get(path)).json()
    assert "case_ids" not in running["lineage"]
    assert running["metrics"]["attempts"] == 1
    assert running["metrics"]["repetitions_requested"] == 1
    assert running["metrics"]["repetitions_completed"] == 0
    response = await client.post(path + "/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["metrics"]["repetitions_completed"] == 0
    assert (await client.get(path)).json()["metrics"]["repetitions_completed"] == 0
    async with app.state.db.write() as session:
        row = await session.get(Run, run_id)
        row.results, row.status, row.gate, row.error = results, "completed", "pass", None
    completed = (await client.get(path)).json()
    assert completed["metrics"]["attempts"] == 2
    assert completed["metrics"]["repetitions_completed"] == 1


@pytest.mark.parametrize("tags", [["split:train"], ["split:test", "split:validation"]])
async def test_historical_split_tags_preserve_payloads_and_hashes(client, app, case_payload, tags):
    response = await client.post("/api/v1/cases", json={"case": case_payload})
    assert response.status_code == 201, response.text
    payload = {**response.json()["case"], "tags": tags}
    # Seed pre-validation data directly; going through today's write API would reject it.
    async with app.state.db.write() as session:
        row = await session.get(CaseRevision, (payload["id"], 1))
        row.payload = payload
        row.status, row.reviewer, row.reason = "approved", "local-synthetic-reviewer", "Historical review"
    canonical = json.dumps([payload], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected_hash = hashlib.sha256(canonical.encode()).hexdigest()
    response = await client.post(
        DEMO + "/dataset-releases",
        json={"name": "Historical", "case_ids": [payload["id"]], "expected_revisions": {payload["id"]: 1}},
    )
    assert response.status_code == 201, response.text
    release = response.json()
    assert release["content_hash"] == expected_hash
    for prefix in ("/api/v1", DEMO):
        assert (await client.get(prefix + "/cases")).json()[0]["case"] == payload
        assert (await client.get(prefix + "/cases/" + payload["id"])).json()["case"] == payload
        assert (await client.get(prefix + "/dataset-releases")).json()[0]["content_hash"] == expected_hash
        detail = (await client.get(prefix + "/dataset-releases/" + release["id"])).json()
        assert detail["cases"] == [payload] and detail["content_hash"] == expected_hash
        params = {} if prefix == "/api/v1" else {"agent_revision_id": "synthetic-fixed", "mode": "mock"}
        response = await client.get(prefix + "/dataset-releases/" + release["id"] + "/export", params=params)
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert json.loads(archive.read("cases.json")) == [payload]
            assert json.loads(archive.read("manifest.json"))["content_hash"] == expected_hash
    response = await client.post(DEMO + "/evaluation-runs", json=run_body(release))
    assert response.status_code == 202, response.text
    await execute(app, response.json()["id"])
    result = (await client.get(DEMO + "/evaluation-runs/" + response.json()["id"])).json()
    assert result["status"] == "completed" and result["gate"] == "pass", result
    assert result["lineage"]["dataset_split"] == "development"
    assert result["lineage"]["case_ids"] == [payload["id"]]
    assert result["lineage"]["content_hash"] == result["lineage"]["release_content_hash"] == expected_hash
    async with app.state.db.sessions() as session:
        assert (await session.get(CaseRevision, (payload["id"], 1))).payload == payload
        assert (await session.get(ReleaseCase, (release["id"], payload["id"]))).payload == payload


@pytest.mark.parametrize("prefix", ["/api/v1", DEMO], ids=["v1", "v2"])
@pytest.mark.parametrize(
    "tags", [["split:train"], ["split:test", "split:validation"], ["split:test", "split:test"]]
)
async def test_new_writes_reject_invalid_split_tags_atomically(client, case_payload, prefix, tags):
    invalid = {**case_payload, "tags": tags}
    response = await client.post(prefix + "/cases", json={"case": invalid})
    assert response.status_code == 422, response.text
    assert (await client.get(prefix + "/cases")).json() == []
    response = await client.post(prefix + "/cases", json={"case": case_payload})
    assert response.status_code == 201, response.text
    original = response.json()
    response = await client.put(
        prefix + "/cases/" + original["case"]["id"],
        json={"case": {**original["case"], "tags": tags}, "expected_revision": 1, "reason": "Invalid split"},
    )
    assert response.status_code == 422, response.text
    assert (await client.get(prefix + "/cases/" + original["case"]["id"])).json() == original
    # A valid row first ensures the invalid row rolls back the entire import.
    raw = 'question,tags\nC-999,split:development\nC-123,"' + ",".join(tags) + '"\n'
    preview = await client.post(prefix + "/imports/preview", files={"file": ("splits.csv", raw.encode())})
    assert preview.status_code == 200, preview.text
    response = await client.post(
        prefix + "/imports/" + preview.json()["id"] + "/commit",
        json={"mapping": {"question": "question", "tags": "tags"}, "duplicate_policy": "new"},
    )
    assert response.status_code == 422, response.text
    assert (await client.get(prefix + "/cases")).json() == [original]
