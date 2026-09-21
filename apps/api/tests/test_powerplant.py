import io
import json
import zipfile

import pytest
from goldenloop_eval import Observation
from goldenloop_eval.bundles import run_bundle

from goldenloop_api.db import ChatSession

from .conftest import wait_for
from .test_projects import publish_v2

FIXTURE = "synthetic-powerplant-v1"
TOOLS = {"commercial_outlook", "risk_assessment", "similar_occurrences"}


async def setup_powerplant(client, variant="fixed"):
    response = await client.post("/api/v2/projects", json={"name": "Synthetic powerplant VaR"})
    assert response.status_code == 201, response.text
    path = "/api/v2/projects/" + response.json()["id"]
    assert (await client.get(path + "/agents")).json() == []
    assert (await client.get(path + "/cases")).json() == []
    response = await client.post(path + "/agents", json={"name": "VaR decision support"})
    assert response.status_code == 201, response.text
    agent = response.json()
    response = await client.post(
        path + f"/agents/{agent['id']}/revisions",
        json={
            "label": variant,
            "spec": {
                "adapter": "synthetic-powerplant-var",
                "variant": variant,
                "fixture_version": FIXTURE,
                "tool_contract": "powerplant-decision-v1",
            },
        },
    )
    assert response.status_code == 201, response.text
    revision = response.json()
    response = await client.post(
        path + "/chat-sessions", json={"title": "VaR investigation", "agent_revision_id": revision["id"]}
    )
    assert response.status_code == 201, response.text
    return path, agent, revision, response.json()


async def send(client, path, chat, content):
    chat_path = path + f"/chat-sessions/{chat['id']}"
    response = await client.post(chat_path + "/messages", json={"content": content})
    assert response.status_code == 202, response.text
    completed = await wait_for(client, chat_path)
    assert completed["status"] == "completed", completed
    assert completed["trace_complete"]
    return completed


@pytest.mark.parametrize("variant", ["fixed", "buggy"])
async def test_powerplant_chat_traces_feedback_and_pinned_revision(client, variant):
    path, agent, revision, chat = await setup_powerplant(client, variant)
    question = "Assess cooling_water and compare operating decisions."
    first = await send(client, path, chat, question)
    assert {call["tool"] for call in first["tool_calls"]} == TOOLS
    assert all(call["turn"] == 0 and call["result"] is not None for call in first["tool_calls"])
    assert all(call["error"] is None for call in first["tool_calls"])
    answer = first["messages"][-1]["content"]
    assert "|" in answer  # The decision result table survives API serialization.
    assert first["agent_revision_id"] == revision["id"]
    assert first["spec_hash"] == revision["spec_hash"]

    # Creating a newer revision must not change this session's fixture or variant.
    response = await client.post(
        path + f"/agents/{agent['id']}/revisions",
        json={"label": "Customer revision", "spec": {"variant": "fixed"}},
    )
    assert response.status_code == 201, response.text
    followup = "Compare the options again for that category."
    second = await send(client, path, chat, followup)
    assert second["messages"][:2] == first["messages"]
    assert second["tool_calls"][: len(first["tool_calls"])] == first["tool_calls"]
    current_calls = [call for call in second["tool_calls"] if call["turn"] == 1]
    assert {call["tool"] for call in current_calls} == TOOLS
    assert len(second["tool_calls"]) == len(first["tool_calls"]) + len(current_calls)
    assert len({call["id"] for call in second["tool_calls"]}) == len(second["tool_calls"])
    assert second["agent_revision_id"] == revision["id"]
    events = await client.get(path + f"/chat-sessions/{chat['id']}/events")
    assert events.status_code == 200
    for tool in TOOLS:
        assert tool in events.text

    response = await client.post(
        path + "/feedback",
        json={
            "session_id": chat["id"],
            "turn": 1,
            "target": "tool",
            "tool_call_id": current_calls[0]["id"],
            "issue_type": "result",
            "comment": "Review the synthetic decision independently",
            "correction": {"answer": "Unreviewed reference must not become ground truth"},
        },
    )
    assert response.status_code == 201, response.text
    candidate_path = path + f"/feedback/{response.json()['id']}/candidate"
    response = await client.post(candidate_path)
    assert response.status_code == 200, response.text
    record = response.json()
    candidate = record["case"]
    assert record["status"] == "candidate"
    assert candidate["fixture_version"] == FIXTURE
    assert candidate["turns"] == [
        {"user": question, "reference_answer": None},
        {"user": followup, "reference_answer": None},
    ]
    assert candidate["checks"] == [] and candidate["context"] == ""
    assert candidate["source"]["agent_revision_id"] == revision["id"]
    assert (await client.post(candidate_path)).json() == record
    assert (await client.get(path + "/dataset-releases")).json() == []
    assert (await client.get("/api/v1/chat-sessions")).json() == []


async def test_powerplant_chat_passes_only_persisted_user_history(client, app, monkeypatch):
    path, _agent, revision, chat = await setup_powerplant(client)
    received = []

    async def invoke(case, *, revision_id, spec, mode):
        received.append(case.model_copy(deep=True))
        assert revision_id == revision["id"]
        assert spec.adapter == "synthetic-powerplant-var"
        return Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode=mode,
            trace_complete=True,
            messages=[
                {"role": "user", "content": case.turns[0].user, "turn": 0},
                {"role": "assistant", "content": "Adapter clarification, not a customer echo", "turn": 0},
            ],
        )

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    questions = ["Investigate cooling_water", "Compare the options", "Which option has less risk?"]
    for turn, question in enumerate(questions):
        completed = await send(client, path, chat, question)
        assert completed["messages"][-1]["content"] == "Adapter clarification, not a customer echo"
        assert completed["messages"][-1]["turn"] == turn
        assert completed["tool_calls"] == []
        current = received[-1]
        assert current.fixture_version == FIXTURE
        assert current.id == chat["id"]
        assert current.context == "\n".join(questions[:turn])
        assert [item.model_dump() for item in current.turns] == [{"user": question, "reference_answer": None}]
        if turn == 0:
            async with app.state.db.write() as session:
                persisted = await session.get(ChatSession, chat["id"])
                persisted.messages = [
                    *persisted.messages[:-1],
                    {**persisted.messages[-1], "content": "Assistant reference mentions fire_smoke"},
                ]
    assert len(received) == 3


async def test_powerplant_release_evaluation_and_portable_export(client, tmp_path):
    path, _agent, revision, _chat = await setup_powerplant(client)
    payload = {
        "id": "powerplant-cooling-water",
        "title": "Synthetic cooling water decision",
        "fixture_version": FIXTURE,
        "context": "Investigate cooling_water",
        "turns": [{"user": "Compare operating decisions for that category."}],
        "checks": [{"kind": "tool_required", "config": {"tool": tool}} for tool in sorted(TOOLS)],
    }
    case, release = await publish_v2(client, path, payload)
    response = await client.post(
        path + "/evaluation-runs",
        json={
            "release_id": release["id"],
            "agent_revision_id": revision["id"],
            "mode": "mock",
            "repetitions": 2,
            "idempotency_key": "powerplant-repeat",
        },
    )
    assert response.status_code == 202, response.text
    run = await wait_for(client, path + f"/evaluation-runs/{response.json()['id']}")
    assert run["status"] == "completed" and run["gate"] == "pass", run
    assert run["lineage"]["fixture_version"] == FIXTURE
    observations = [item["observation"] for item in run["results"]]
    assert len(observations) == 2
    assert observations[0]["messages"] == observations[1]["messages"]
    results = [{call["tool"]: call["result"] for call in item["tool_calls"]} for item in observations]
    assert set(results[0]) == TOOLS and results[0] == results[1]
    response = await client.get(
        path + f"/dataset-releases/{release['id']}/export",
        params={"agent_revision_id": revision["id"], "mode": "mock"},
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["agent_spec"]["fixture_version"] == FIXTURE
        assert manifest["agent_spec_hash"] == revision["spec_hash"]
        assert json.loads(archive.read("cases.json")) == [case]
        archive.extractall(tmp_path / "bundle")
    assert (await run_bundle(tmp_path / "bundle"))["gate"] == "pass"
