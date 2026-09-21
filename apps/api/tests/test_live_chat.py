import asyncio
import json
import os

import httpx
import pytest
from goldenloop_eval import Observation, ToolCall
from sqlalchemy import select

from goldenloop_api.config import invocation_secrets
from goldenloop_api.db import ChatSession, Event, MessageCommand
from goldenloop_api.main import create_app

from .conftest import wait_for
from .test_projects import setup_project


async def live_revision(client, app, monkeypatch, *, number=0, adapter="synthetic-customer"):
    object.__setattr__(app.state.settings, "allow_live", True)
    monkeypatch.setattr("goldenloop_api.registry.importlib.util.find_spec", lambda name: object())
    path, agent, mock = await setup_project(client, f"Live playground {number}")
    connection = {
        "endpoint": f"https://playground-{number}.example.com",
        "deployment": f"deployment-{number}",
        "api_version": "v1",
        "auth": "api_key",
        "binding": f"playground-{number}",
    }
    key = f"opaque-playground-value-{number}"
    env_name = f"PLAYGROUND_CREDENTIAL_{number}"
    monkeypatch.setenv(env_name, key)
    bindings = json.loads(os.environ.get("GOLDENLOOP_CONNECTION_BINDINGS", "{}"))
    bindings[connection["binding"]] = {
        "endpoint": connection["endpoint"],
        "auth": "api_key",
        "key_env": env_name,
        "projects": [agent["project_id"]],
    }
    monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", json.dumps(bindings))
    spec = {"variant": "fixed", "adapter": adapter, "modes": ["mock", "live"], "connection": connection}
    if adapter == "synthetic-powerplant-var":
        spec.update(fixture_version="synthetic-powerplant-v1", tool_contract="powerplant-decision-v1")
    response = await client.post(
        path + f"/agents/{agent['id']}/revisions", json={"label": "Live", "spec": spec}
    )
    assert response.status_code == 201, response.text
    return path, response.json(), mock, key, env_name


async def create_chat(client, path, revision, **kwargs):
    response = await client.post(
        path + "/chat-sessions", json={"agent_revision_id": revision["id"], **kwargs}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("adapter", ["synthetic-customer", "synthetic-powerplant-var"])
async def test_live_chat_history_mode_persistence_and_redaction(client, app, monkeypatch, adapter):
    path, revision, _mock, key, env_name = await live_revision(client, app, monkeypatch, adapter=adapter)
    chat = await create_chat(client, path, revision, mode="live")
    assert chat["mode"] == "live"
    chat_path = path + f"/chat-sessions/{chat['id']}"
    invocations = []

    async def invoke(case, *, revision_id, spec, mode, api_key, history):
        assert mode == "live" and revision_id == revision["id"]
        assert api_key == key and invocation_secrets.get() == (key,)
        assert spec.connection.model_dump() == revision["spec"]["connection"]
        assert case.fixture_version == revision["spec"]["fixture_version"]
        assert len(case.turns) == 1 and case.turns[0].reference_answer is None
        invocations.append((case.model_copy(deep=True), history))
        # Rotate out of the environment before returning evidence. Redaction must use
        # the invocation snapshot, not just the current binding's credential.
        monkeypatch.setenv(env_name, "rotated-playground-value")
        return Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode=mode,
            trace_complete=True,
            messages=[
                {"role": "user", "content": case.turns[0].user, "turn": 0},
                {"role": "assistant", "content": f"Live answer {len(invocations)} {key}", "turn": 0},
            ],
            tool_calls=[ToolCall(id="current", tool="risk_assessment", turn=0, result={"value": key})]
            if len(invocations) == 1
            else [],
        )

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    for question in ("Investigate cooling_water", "Which option has lower risk?"):
        monkeypatch.setenv(env_name, key)
        response = await client.post(chat_path + "/messages", json={"content": question})
        assert response.status_code == 202, response.text
        result = await wait_for(client, chat_path)
        assert result["status"] == "completed" and result["mode"] == "live", result
        assert key not in json.dumps(result)
    assert len(invocations) == 2
    assert invocations[0][1] == []
    assert invocations[1][1] == [{"role": m["role"], "content": m["content"]} for m in result["messages"][:2]]
    assert invocations[1][0].turns[0].user == "Which option has lower risk?"
    assert all(case.context == "" for case, _history in invocations)
    assert result["messages"][-1]["content"].startswith("Live answer 2 ")
    assert len(result["tool_calls"]) == 1 and result["tool_calls"][0]["turn"] == 0
    assert result["tool_calls"][0]["result"] == {"value": "[REDACTED]"}
    assert invocation_secrets.get() == ()
    events = await client.get(chat_path + "/events")
    assert key not in events.text and "[REDACTED]" in events.text
    async with app.state.db.sessions() as session:
        persisted = await session.get(ChatSession, chat["id"])
        assert persisted.mode == "live"
        assert key not in json.dumps([persisted.messages, persisted.tool_calls])
        evidence = (await session.scalars(select(Event))).all()
        assert key not in json.dumps([event.payload for event in evidence])
    # A new application/connection reads the same pinned mode and evidence.
    reopened = create_app(app.state.settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reopened), base_url="http://localhost"
        ) as reader:
            assert (await reader.get(chat_path)).json() == result
            assert (await reader.get(path + "/chat-sessions")).json()[0]["mode"] == "live"
    finally:
        await reopened.state.db.engine.dispose()


async def test_chat_modes_and_live_readiness_fail_closed(client, app, monkeypatch):
    path, live, mock, _key, env_name = await live_revision(client, app, monkeypatch)
    assert (await create_chat(client, path, live))["mode"] == "mock"
    assert (await create_chat(client, path, mock))["mode"] == "mock"
    response = await client.post(
        path + "/chat-sessions", json={"agent_revision_id": mock["id"], "mode": "live"}
    )
    assert response.status_code == 409
    chat = await create_chat(client, path, live, mode="live")
    monkeypatch.delenv(env_name)
    for endpoint, body in (
        (path + "/chat-sessions", {"agent_revision_id": live["id"], "mode": "live"}),
        (path + f"/chat-sessions/{chat['id']}/messages", {"content": "C-123"}),
    ):
        response = await client.post(endpoint, json=body)
        assert response.status_code == 409, response.text
    stored = (await client.get(path + f"/chat-sessions/{chat['id']}")).json()
    assert stored["mode"] == "live" and stored["status"] == "idle" and stored["messages"] == []
    assert (await create_chat(client, path, live, mode="mock"))["mode"] == "mock"
    monkeypatch.setenv(env_name, "available-again")
    object.__setattr__(app.state.settings, "allow_live", False)
    assert (
        await client.post(path + "/chat-sessions", json={"agent_revision_id": live["id"], "mode": "live"})
    ).status_code == 409


@pytest.mark.parametrize("failure", ["tool", "provider", "incomplete", "tool_error_only"])
async def test_live_failed_turn_preserves_redacted_partial_evidence(client, app, monkeypatch, failure):
    path, revision, _mock, key, env_name = await live_revision(
        client, app, monkeypatch, adapter="synthetic-powerplant-var"
    )
    chat = await create_chat(client, path, revision, mode="live")
    chat_path = path + f"/chat-sessions/{chat['id']}"
    invocations = []

    async def invoke(case, *, revision_id, spec, mode, api_key, history):
        assert api_key == key and mode == "live" and case.context == ""
        assert len(case.turns) == 1
        invocations.append(history)
        messages = [{"role": "user", "content": case.turns[0].user, "turn": 0}]
        calls = [ToolCall(id="outlook", tool="commercial_outlook", turn=0, result={"value": key})]
        if len(invocations) == 1:
            messages.append({"role": "assistant", "content": "Previous observed answer", "turn": 0})
            return Observation(
                case_id=case.id,
                case_revision=case.revision,
                agent_revision=revision_id,
                mode=mode,
                messages=messages,
                tool_calls=calls,
                trace_complete=True,
            )
        # An earlier tool succeeded, then the tool/provider failed. Keep the original
        # credential in evidence after rotation to exercise invocation-scoped redaction.
        monkeypatch.setenv(env_name, "rotated-playground-value")
        if failure in {"tool", "tool_error_only"}:
            calls.append(
                ToolCall(
                    id="risk",
                    parent_id="outlook",
                    tool="risk_assessment",
                    turn=0,
                    arguments={"category": "cooling_water", "diagnostic": key},
                    error="Tool failed " + key,
                )
            )
        if failure == "tool_error_only":
            messages.append({"role": "assistant", "content": "Observed tool failure " + key, "turn": 0})
        return Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode=mode,
            messages=messages,
            tool_calls=calls,
            trace_complete=failure == "tool_error_only",
            error="Provider or tool failed " + key if failure in {"provider", "tool"} else None,
        )

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    response = await client.post(chat_path + "/messages", json={"content": "Assess cooling_water"})
    assert response.status_code == 202, response.text
    first = await wait_for(client, chat_path)
    assert first["status"] == "completed"
    response = await client.post(chat_path + "/messages", json={"content": "Assess the risk again"})
    assert response.status_code == 202, response.text
    command_id = response.json()["id"]
    result = await wait_for(client, chat_path)
    assert result["status"] == "failed" and result["trace_complete"] is False
    assert result["error"] == "Chat execution failed or timed out"
    assert result["messages"][:2] == first["messages"]
    assert result["tool_calls"][:1] == first["tool_calls"]
    current_messages = result["messages"][2:]
    assert [m["role"] for m in current_messages] == (
        ["user", "assistant"] if failure == "tool_error_only" else ["user"]
    )
    assert all(m["turn"] == 1 and m["command_id"] == command_id for m in current_messages)
    calls = result["tool_calls"][1:]
    assert calls[0]["result"] == {"value": "[REDACTED]"}
    assert calls[0]["id"] == f"{command_id}:call:0"
    assert all(call["turn"] == 1 for call in calls)
    if failure in {"tool", "tool_error_only"}:
        assert calls[1]["error"] == "Tool failed [REDACTED]"
        assert calls[1]["arguments"]["diagnostic"] == "[REDACTED]"
        assert calls[1]["parent_id"] == calls[0]["id"]
    assert key not in json.dumps(result)
    events = await client.get(chat_path + "/events")
    assert key not in events.text and "commercial_outlook" in events.text
    assert '"status": "failed"' in events.text and '"trace_complete": false' in events.text
    async with app.state.db.sessions() as session:
        command = await session.get(MessageCommand, command_id)
        persisted = await session.get(ChatSession, chat["id"])
        assert command.status == persisted.status == "failed"
        assert persisted.messages == result["messages"] and persisted.tool_calls == result["tool_calls"]
        rows = (await session.scalars(select(Event).where(Event.stream == "chat:" + chat["id"]))).all()
        assert key not in json.dumps([row.payload for row in rows])
    assert len(invocations) == 2
    assert invocations[1] == [{"role": m["role"], "content": m["content"]} for m in first["messages"]]
    feedback = {
        "session_id": chat["id"],
        "turn": 1,
        "target": "tool",
        "tool_call_id": calls[-1]["id"],
        "issue_type": "execution",
        "comment": "Review the preserved call from the incomplete turn",
    }
    response = await client.post(path + "/feedback", json=feedback)
    assert response.status_code == 201, response.text
    assert response.json()["tool_call_id"] == calls[-1]["id"]
    assert response.json()["status"] == "unresolved"
    for invalid in (
        {"tool_call_id": "unobserved-call"},
        {"tool_call_id": first["tool_calls"][0]["id"]},
        {"tool_call_id": None},
        {"turn": 2},
    ):
        assert (await client.post(path + "/feedback", json={**feedback, **invalid})).status_code == 422
    answer_feedback = {**feedback, "target": "answer", "tool_call_id": None}
    response = await client.post(path + "/feedback", json=answer_feedback)
    assert response.status_code == (201 if failure == "tool_error_only" else 422), response.text
    assert (await client.get(chat_path)).json() == result  # Review never fabricates an answer.
    assert (await client.post(chat_path + "/messages", json={"content": "Continue"})).status_code == 409
    assert invocation_secrets.get() == ()


async def test_tool_feedback_requires_observed_user_turn(client, app, monkeypatch):
    path, revision, _mock, _key, _env = await live_revision(client, app, monkeypatch)
    chat = await create_chat(client, path, revision, mode="live")

    async def invoke(case, *, revision_id, mode, **kwargs):
        return Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode=mode,
            trace_complete=False,
            error="Incomplete invocation",
            tool_calls=[ToolCall(id="call", tool="risk_assessment", turn=0, error="Tool failed")],
        )

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    chat_path = path + f"/chat-sessions/{chat['id']}"
    response = await client.post(chat_path + "/messages", json={"content": "Assess cooling_water"})
    assert response.status_code == 202, response.text
    result = await wait_for(client, chat_path)
    assert result["status"] == "failed" and result["messages"] == []
    assert len(result["tool_calls"]) == 1
    response = await client.post(
        path + "/feedback",
        json={
            "session_id": chat["id"],
            "turn": 0,
            "target": "tool",
            "tool_call_id": result["tool_calls"][0]["id"],
            "issue_type": "execution",
            "comment": "Call without an observed user turn",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "malformed", ["roles", "content", "message_turn", "tool_turn", "duplicate", "lineage", "size"]
)
async def test_partial_live_malformed_evidence_is_not_persisted(client, app, monkeypatch, malformed):
    path, revision, _mock, _key, _env = await live_revision(client, app, monkeypatch)
    chat = await create_chat(client, path, revision, mode="live")

    async def invoke(case, *, revision_id, mode, **kwargs):
        observation = Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode=mode,
            trace_complete=False,
            error="Failed invocation",
            messages=[{"role": "user", "content": case.turns[0].user, "turn": 0}],
            tool_calls=[ToolCall(id="call", tool="risk_assessment", turn=0, result={"value": "observed"})],
        )
        if malformed == "roles":
            observation.messages[0]["role"] = "assistant"
        elif malformed == "content":
            observation.messages[0]["content"] = {"unexpected": "object"}
        elif malformed == "message_turn":
            observation.messages[0]["turn"] = 1
        elif malformed == "tool_turn":
            observation.tool_calls[0].turn = 1
        elif malformed == "duplicate":
            observation.tool_calls.append(observation.tool_calls[0].model_copy())
        elif malformed == "lineage":
            observation.case_id = "wrong-session"
        else:
            observation.tool_calls[0].result = "x" * (1024 * 1024)
        return observation

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    chat_path = path + f"/chat-sessions/{chat['id']}"
    response = await client.post(chat_path + "/messages", json={"content": "Investigate cooling_water"})
    assert response.status_code == 202, response.text
    result = await wait_for(client, chat_path)
    assert result["status"] == "failed" and result["trace_complete"] is False
    assert result["messages"] == [] and result["tool_calls"] == []
    events = await client.get(chat_path + "/events")
    assert "event: message\n" not in events.text and "event: tool_call\n" not in events.text


async def test_live_chat_concurrent_project_credentials_are_isolated(client, app, monkeypatch):
    projects = [await live_revision(client, app, monkeypatch, number=number) for number in range(2)]
    app.state.runner.stopping = True
    await asyncio.sleep(0.03)
    pending = []
    for path, revision, _mock, key, env_name in projects:
        chat = await create_chat(client, path, revision, mode="live")
        response = await client.post(
            path + f"/chat-sessions/{chat['id']}/messages", json={"content": "C-123"}
        )
        assert response.status_code == 202, response.text
        pending.append((path, chat, response.json()["id"]))
    expected = {revision["id"]: (key, env_name) for _path, revision, _mock, key, env_name in projects}
    ready = asyncio.Event()
    arrived = []

    async def invoke(case, *, revision_id, spec, mode, api_key, history):
        key, env_name = expected[revision_id]
        assert api_key == key and history == [] and mode == "live"
        assert invocation_secrets.get() == (key,)
        arrived.append(revision_id)
        monkeypatch.delenv(env_name)
        if len(arrived) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 1)
        assert invocation_secrets.get() == (key,)
        return Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode=mode,
            trace_complete=True,
            messages=[
                {"role": "user", "content": case.turns[0].user, "turn": 0},
                {"role": "assistant", "content": "Response " + key, "turn": 0},
            ],
        )

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    async with app.state.db.write() as session:
        for _path, _chat, command_id in pending:
            command = await session.get(MessageCommand, command_id)
            command.status = "running"
    await asyncio.gather(*(app.state.runner.execute_chat(command_id) for _path, _chat, command_id in pending))
    assert len(arrived) == 2 and invocation_secrets.get() == ()
    for path, chat, _command_id in pending:
        response = await client.get(path + f"/chat-sessions/{chat['id']}")
        assert response.json()["status"] == "completed"
        assert response.json()["messages"][-1]["content"] == "Response [REDACTED]"
        assert all(key not in response.text for key, _env in expected.values())


async def test_live_playground_real_framework_fake_transport(client, app, monkeypatch):
    pytest.importorskip("agent_framework.openai")
    path, revision, _mock, key, _env = await live_revision(client, app, monkeypatch)
    chat = await create_chat(client, path, revision, mode="live")
    requests, transports = [], []
    real_client = httpx.AsyncClient

    async def respond(request):
        assert request.url.host == "playground-0.example.com"
        assert request.headers["api-key"] == key
        body = json.loads(request.content)
        requests.append(body)
        number = len(requests)
        if number == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "lookup-current",
                        "type": "function",
                        "function": {
                            "name": "lookup_customer",
                            "arguments": json.dumps({"customer_id": "C-123"}),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": f"Generated live answer {number}: C-123"}
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": f"response-{number}",
                "object": "chat.completion",
                "created": 0,
                "model": "deployment-0",
                "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            },
        )

    class TransportClient(real_client):
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            transports.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", TransportClient)
    chat_path = path + f"/chat-sessions/{chat['id']}"
    for content in ("Look up C-123", "Which customer was it?"):
        response = await client.post(chat_path + "/messages", json={"content": content})
        assert response.status_code == 202, response.text
        result = await wait_for(client, chat_path)
        assert result["status"] == "completed", result
    assert len(requests) == 3  # One tool loop, then one history-aware follow-up.
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "lookup_customer"
    assert result["tool_calls"][0]["turn"] == 0
    history = requests[-1]["messages"]
    assert any(
        m["role"] == "assistant" and "Generated live answer 2" in str(m.get("content")) for m in history
    )
    assert sum(m["role"] == "user" for m in history) == 2
    assert all(m["role"] != "tool" and not m.get("tool_calls") for m in history)
    assert result["messages"][-1]["content"] == "Generated live answer 3: C-123"
    assert len(transports) == 2 and all(transport.is_closed for transport in transports)


@pytest.mark.parametrize("failure", ["binding_removed", "provider_error", "wrong_mode"])
async def test_queued_live_chat_failures_never_fall_back(client, app, monkeypatch, failure):
    path, revision, _mock, key, _env = await live_revision(client, app, monkeypatch)
    chat = await create_chat(client, path, revision, mode="live")
    # Stop queue polling to change readiness after acceptance but before execution.
    app.state.runner.stopping = True
    await asyncio.sleep(0.03)
    chat_path = path + f"/chat-sessions/{chat['id']}"
    response = await client.post(chat_path + "/messages", json={"content": "Investigate cooling_water"})
    assert response.status_code == 202, response.text
    invoked = []

    async def invoke(case, *, revision_id, mode, **kwargs):
        invoked.append(mode)
        if failure == "provider_error":
            raise RuntimeError("Provider failure " + key)
        return Observation(
            case_id=case.id,
            case_revision=case.revision,
            agent_revision=revision_id,
            mode="mock",
            trace_complete=True,
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError("Mock fallback is forbidden")

    monkeypatch.setattr("goldenloop_api.runner.run_revision", invoke)
    monkeypatch.setattr("goldenloop_api.runner.run_case", forbidden)
    if failure == "binding_removed":
        monkeypatch.setenv("GOLDENLOOP_CONNECTION_BINDINGS", "{}")
    async with app.state.db.write() as session:
        command = await session.get(MessageCommand, response.json()["id"])
        command.status = "running"
    await app.state.runner.execute_chat(response.json()["id"])
    result = (await client.get(chat_path)).json()
    assert result["mode"] == "live" and result["status"] == "failed"
    assert not result["trace_complete"] and result["messages"] == [] and result["tool_calls"] == []
    assert key not in json.dumps(result)
    assert invoked == ([] if failure == "binding_removed" else ["live"])
    assert invocation_secrets.get() == ()
