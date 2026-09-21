"""Synthetic VaR contracts and real Agent Framework execution over a fake HTTP transport."""
import asyncio
import csv
import io
import json
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files

import pytest

from goldenloop_demo_agent import run_revision
from goldenloop_demo_agent import powerplant
from goldenloop_eval import AgentConnection, AgentSpec, Case, evaluate


CATEGORIES = ("cooling_water", "fuel_gas", "steam_water_leak", "fire_smoke")
TOOLS = ("commercial_outlook", "risk_assessment", "similar_occurrences")
FIXTURE = "synthetic-powerplant-v1"
TABLE_COLUMNS = ("incident_id", "category", "component", "symptoms", "action_taken", "outcome")
TABLE_HEADER = "| ID | Category | Component | Symptoms | Action taken | Outcome |"


def spec(**updates):
    return AgentSpec.model_validate({
        "adapter": "synthetic-powerplant-var", "variant": "fixed",
        "fixture_version": FIXTURE, "tool_contract": "powerplant-decision-v1", **updates,
    })


def case(**updates):
    return Case.model_validate({
        "id": "synthetic-var-case", "revision": 3, "title": "Synthetic VaR investigation",
        "fixture_version": FIXTURE, "turns": [{"user": "Assess cooling_water"}],
        "checks": [{"id": tool, "kind": "tool_required", "config": {"tool": tool}} for tool in TOOLS],
        **updates,
    })


@pytest.fixture
def incident_rows():
    resource = files("goldenloop_demo_agent").joinpath("data/powerplant_incidents.csv")
    with resource.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert set(reader.fieldnames) == {*TABLE_COLUMNS, "synthetic"}
        return list(reader)


def test_packaged_csv_has_three_to_seven_complete_synthetic_rows_per_category(incident_rows):
    counts = Counter(row["category"] for row in incident_rows)
    assert set(counts) == set(CATEGORIES)
    assert all(3 <= count <= 7 for count in counts.values())
    assert len({row["incident_id"] for row in incident_rows}) == len(incident_rows)
    for row in incident_rows:
        assert set(row) == {*TABLE_COLUMNS, "synthetic"}
        assert all(isinstance(value, str) and value.strip() for value in row.values())
        assert row["synthetic"] == "true"


@pytest.mark.parametrize("category", CATEGORIES)
def test_similar_occurrences_is_exact_csv_lookup_and_returns_fresh_rows(incident_rows, category):
    expected = {
        "category": category, "synthetic": True, "source": FIXTURE,
        "incidents": [{**row, "synthetic": True} for row in incident_rows if row["category"] == category],
    }
    result = powerplant.similar_occurrences(category)
    assert result == expected
    result["incidents"][0]["outcome"] = "mutated caller data"
    result["incidents"].clear()
    assert powerplant.similar_occurrences(category) == expected


@pytest.mark.parametrize("corruption", ["missing_column", "extra_column", "empty_value",
                                        "unknown_category", "nonsynthetic", "short_row", "long_row"])
def test_lookup_rejects_invalid_csv_even_in_an_unrequested_category(monkeypatch, incident_rows, corruption):
    columns = [*TABLE_COLUMNS, "synthetic"]
    rows = [dict(row) for row in incident_rows]
    # Corrupt a different category: validation must cover the source, not just returned matches.
    row = next(row for row in rows if row["category"] == "fire_smoke")
    if corruption == "missing_column":
        columns.remove("outcome")
    elif corruption == "extra_column":
        columns.append("unexpected")
    elif corruption == "empty_value":
        row["component"] = ""
    elif corruption == "unknown_category":
        row["category"] = "other"
    elif corruption == "nonsynthetic":
        row["synthetic"] = "false"
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(columns)
    for item in rows:
        values = [item.get(column, "unexpected") for column in columns]
        if item is row and corruption == "short_row":
            values.pop()
        elif item is row and corruption == "long_row":
            values.append("extra")
        writer.writerow(values)

    class Resource:
        def joinpath(self, path):
            assert path == "data/powerplant_incidents.csv"
            return self

        def open(self, *args, **kwargs):
            return io.StringIO(stream.getvalue())

    monkeypatch.setattr(powerplant, "files", lambda package: Resource())
    with pytest.raises(ValueError, match="Invalid .*incident CSV"):
        powerplant.similar_occurrences("cooling_water")


@pytest.mark.parametrize("category", ["", "cooling", "Cooling_water", "cooling_water ",
                                     " cooling_water", "fuel_gas,fire_smoke", "unknown"])
@pytest.mark.parametrize("tool_index", range(3), ids=TOOLS)
def test_tools_reject_unknown_or_nonexact_categories(category, tool_index):
    tool = powerplant.make_tools("invalid-category")[tool_index]
    with pytest.raises(ValueError, match="Unknown incident category"):
        tool(category)


@pytest.mark.parametrize("category", CATEGORIES)
def test_ratings_are_synthetic_and_cover_all_levels_across_seeds(category):
    returns, risks = set(), set()
    for seed in range(128):
        commercial, risk, incidents = powerplant.make_tools(f"coverage-{seed}")
        assert (commercial.__name__, risk.__name__, incidents.__name__) == TOOLS
        outlook, assessment = commercial(category), risk(category)
        assert outlook == {
            "category": category, "return": outlook["return"], "horizon": "today",
            "synthetic": True, "method": "seeded-random-v1",
        }
        assert assessment == {
            "category": category, "risk": assessment["risk"], "synthetic": True,
            "method": "seeded-random-v1", "valid_for_operations": False,
        }
        returns.add(outlook["return"])
        risks.add(assessment["risk"])
    assert returns == {"no", "low", "medium", "high"}
    assert risks == {"none", "low", "medium", "high"}


def test_seeded_tools_are_repeatable_order_independent_and_do_not_share_rng():
    tools = powerplant.make_tools("stable-seed")
    expected = {(tool.__name__, category): tool(category) for tool in tools for category in CATEGORIES}
    state = random.getstate()
    try:
        random.seed(17)
        seeded_state = random.getstate()
        for tool in reversed(tools):
            for category in reversed(CATEGORIES):
                for other in powerplant.make_tools("interleaved-seed"):
                    other(category)
                assert tool(category) == expected[tool.__name__, category]
                assert tool(category) == expected[tool.__name__, category]
        assert random.getstate() == seeded_state
        random.seed(999)
        fresh = powerplant.make_tools("stable-seed")
        jobs = [(tool, category) for _ in range(8) for tool in fresh for category in CATEGORIES]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda job: job[0](job[1]), jobs))
        assert results == [expected[tool.__name__, category] for tool, category in jobs]
    finally:
        random.setstate(state)


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("variant", ["fixed", "buggy"])
def test_mock_revision_records_all_tools_ratings_and_exact_incident_table(category, variant):
    current = case(turns=[{"user": f"Assess {category}", "reference_answer": "REFERENCE MUST NOT BE USED"}])
    obs = asyncio.run(run_revision(current, "immutable-var-revision", spec(variant=variant)))
    assert (obs.case_id, obs.case_revision, obs.agent_revision, obs.mode) == (
        current.id, current.revision, "immutable-var-revision", "mock",
    )
    assert obs.error is None and obs.trace_complete
    assert evaluate(current, obs).gate == "pass"
    assert [call.tool for call in obs.tool_calls] == list(TOOLS)
    assert len({call.id for call in obs.tool_calls}) == 3
    expected = {tool.__name__: tool(category) for tool in powerplant.make_tools(current.id)}
    for call in obs.tool_calls:
        assert call.arguments == {"category": category}
        assert call.result == expected[call.tool]
        assert call.error is None and call.turn == 0
        assert call.latency_ms is not None and call.latency_ms >= 0
    assert obs.messages[0] == {"role": "user", "content": current.turns[0].user, "turn": 0}
    answer = obs.messages[1]
    assert answer["role"] == "assistant" and answer["turn"] == 0
    assert "mock test mode (no LLM)" in answer["content"]
    assert "Synthetic demo only" in answer["content"]
    assert f"Mock technical risk: {expected['risk_assessment']['risk']}." in answer["content"]
    assert f"Mock commercial return today: {expected['commercial_outlook']['return']}." in answer["content"]
    table_lines = [line for line in answer["content"].splitlines() if line.startswith("|")]
    assert table_lines == [TABLE_HEADER, "| --- | --- | --- | --- | --- | --- |", *[
        "| " + " | ".join(row[key] for key in TABLE_COLUMNS) + " |"
        for row in expected["similar_occurrences"]["incidents"]
    ]]
    assert "REFERENCE MUST NOT BE USED" not in obs.model_dump_json()
    repeat = asyncio.run(run_revision(current, "immutable-var-revision", spec(variant=variant)))
    assert repeat.messages == obs.messages
    assert [call.result for call in repeat.tool_calls] == [call.result for call in obs.tool_calls]


def test_mock_routes_current_user_then_prior_user_context_without_reference_answers():
    current = case(context="Earlier report: cooling_water", turns=[
        {"user": "Compare options for that issue", "reference_answer": "fire_smoke REFERENCE ONLY"},
        {"user": "Now investigate fuel_gas", "reference_answer": "steam_water_leak REFERENCE ONLY"},
    ])
    obs = asyncio.run(run_revision(current, "revision", spec()))
    assert obs.error is None
    assert [(call.turn, call.arguments["category"]) for call in obs.tool_calls] == (
        [(0, "cooling_water")] * 3 + [(1, "fuel_gas")] * 3
    )
    assert [message["turn"] for message in obs.messages] == [0, 0, 1, 1]
    assert "REFERENCE ONLY" not in obs.model_dump_json()
    # A later ambiguous turn uses actual prior user text, not its golden correction.
    followup = case(turns=[
        {"user": "Investigate fuel_gas", "reference_answer": "fire_smoke"},
        {"user": "Compare the options again", "reference_answer": "steam_water_leak"},
    ])
    followup_obs = asyncio.run(run_revision(followup, "revision", spec()))
    assert [(call.turn, call.arguments["category"]) for call in followup_obs.tool_calls] == (
        [(0, "fuel_gas")] * 3 + [(1, "fuel_gas")] * 3
    )
    assert len({call.id for call in followup_obs.tool_calls}) == 6


def test_mock_ambiguous_user_cannot_route_from_reference_answer():
    current = case(turns=[{"user": "What should we consider?", "reference_answer": "Investigate fire_smoke"}])
    obs = asyncio.run(run_revision(current, "revision", spec()))
    assert obs.error is None and obs.trace_complete
    assert obs.tool_calls == []
    assert "Which problem category applies" in obs.messages[-1]["content"]
    assert evaluate(current, obs).gate == "fail"  # Required tools were not observed.


def test_mock_reports_each_current_category_without_hiding_fire_or_gas():
    current = case(turns=[{"user": "Assess cooling_water, fuel_gas, steam_water_leak, and fire_smoke"}])
    obs = asyncio.run(run_revision(current, "revision", spec()))
    assert obs.error is None
    assert Counter((call.arguments["category"], call.tool) for call in obs.tool_calls) == Counter(
        (category, tool) for category in CATEGORIES for tool in TOOLS
    )
    assert obs.messages[-1]["content"].count(TABLE_HEADER) == 4
    assert "random rating never clears a hazard" in obs.messages[-1]["content"]


@pytest.mark.parametrize("failed_tool", TOOLS)
def test_mock_tool_errors_are_traced_and_block_evaluation(monkeypatch, failed_tool):
    original = powerplant.make_tools

    def broken_tools(seed):
        def fail(category):
            raise RuntimeError("private-provider-detail")
        fail.__name__ = failed_tool
        return [fail if tool.__name__ == failed_tool else tool for tool in original(seed)]

    monkeypatch.setattr(powerplant, "make_tools", broken_tools)
    current = case()
    obs = asyncio.run(run_revision(current, "revision", spec()))
    assert obs.error == "Powerplant tool execution failed"
    assert obs.trace_complete  # Every attempted call, including the failure, was observed.
    assert [call.tool for call in obs.tool_calls] == list(TOOLS)
    for call in obs.tool_calls:
        assert call.latency_ms is not None and call.latency_ms >= 0
        if call.tool == failed_tool:
            assert call.error == "Synthetic tool execution failed" and call.result is None
        else:
            assert call.error is None and call.result is not None
    assert "Tool evidence unavailable; no assessment can be completed." in obs.messages[-1]["content"]
    assert TABLE_HEADER not in obs.messages[-1]["content"]
    assert "private-provider-detail" not in obs.model_dump_json()
    assert evaluate(current, obs).gate == "error"


@pytest.mark.parametrize("category", CATEGORIES)
def test_real_framework_invokes_all_var_tools_with_history_and_no_prior_tool_replay(monkeypatch, category):
    pytest.importorskip("agent_framework.openai")
    import httpx

    requests, clients = [], []
    real_client = httpx.AsyncClient
    current = case(context="Synthetic shift B", turns=[
        {"user": f"Assess {category}", "reference_answer": "REFERENCE NEVER SENT TO MODEL"},
        {"user": "Summarize those findings", "reference_answer": "SECOND REFERENCE NEVER SENT"},
    ])
    expected = {tool.__name__: tool(category) for tool in powerplant.make_tools(current.id)}
    table = "\n".join([TABLE_HEADER, "| --- | --- | --- | --- | --- | --- |", *[
        "| " + " | ".join(row[key] for key in TABLE_COLUMNS) + " |"
        for row in expected["similar_occurrences"]["incidents"]
    ]])
    answer = (f"Synthetic/demo findings: risk {expected['risk_assessment']['risk']}; "
              f"commercial return today {expected['commercial_outlook']['return']}.\n\n{table}")

    def respond(request):
        requests.append(request)
        number = len(requests)
        if number == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"var-call-{tool}", "type": "function", "function": {
                    "name": tool, "arguments": json.dumps({"category": category}),
                }} for tool in TOOLS
            ]}
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": answer if number == 2 else "Synthetic follow-up summary"}
            finish_reason = "stop"
        return httpx.Response(200, json={
            "id": f"response-{number}", "object": "chat.completion", "created": 0,
            "model": "var-unit-model", "choices": [{
                "index": 0, "message": message, "finish_reason": finish_reason,
            }], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "wrong-global-key")
    live_spec = spec(modes=["live"], instructions="Use concise shift summaries.", connection=AgentConnection(
        endpoint="https://var-unit.openai.azure.com", deployment="var-unit-model",
        api_version="2025-01-01-preview", auth="api_key", binding="var-test",
    ).model_dump())
    history = [
        {"role": "user", "content": "Earlier report: fuel_gas"},
        {"role": "assistant", "content": "Earlier generated synthetic assessment.", "tool_calls": [{
            "id": "old-call-not-to-replay", "type": "function", "function": {
                "name": "risk_assessment", "arguments": '{"category":"fuel_gas"}',
            },
        }]},
    ]
    obs = asyncio.run(run_revision(current, "immutable-live-var", live_spec, "live",
                                   api_key="var-test-credential", history=history))
    assert obs.error is None, obs.error
    assert len(requests) == 3
    assert clients and all(client.is_closed for client in clients)
    for request in requests:
        assert request.url.host == "var-unit.openai.azure.com"
        assert "/deployments/var-unit-model/" in request.url.path
        assert request.url.params["api-version"] == "2025-01-01-preview"
        assert request.headers["api-key"] == "var-test-credential"
        assert "authorization" not in request.headers
    payloads = [json.loads(request.content) for request in requests]
    schemas = {tool["function"]["name"]: tool["function"] for tool in payloads[0]["tools"]}
    assert set(schemas) == set(TOOLS)
    for schema in schemas.values():
        assert schema["description"]
        parameters = schema["parameters"]
        assert parameters["type"] == "object"
        assert parameters["required"] == ["category"]
        assert set(parameters["properties"]) == {"category"}
        assert parameters["properties"]["category"]["type"] == "string"
        assert set(parameters["properties"]["category"]["enum"]) == set(CATEGORIES)
    first_messages = payloads[0]["messages"]
    assert [message["role"] for message in first_messages] == ["system", "user", "assistant", "user"]
    assert "VaR" in first_messages[0]["content"]
    assert "Use concise shift summaries." in first_messages[0]["content"]
    assert first_messages[1]["content"] == history[0]["content"]
    assert first_messages[2]["content"] == history[1]["content"]
    assert "Synthetic shift B" in first_messages[-1]["content"]
    assert current.turns[0].user in first_messages[-1]["content"]
    assert all("tool_calls" not in message for message in first_messages)
    results = [message for message in payloads[1]["messages"] if message["role"] == "tool"]
    assert len(results) == 3
    assert {message["tool_call_id"]: json.loads(message["content"]) for message in results} == {
        f"var-call-{tool}": result for tool, result in expected.items()
    }
    assert payloads[2]["messages"][-2:] == [
        {"role": "assistant", "name": "VaR", "content": answer},
        {"role": "user", "content": current.turns[1].user},
    ]
    serialized = json.dumps(payloads)
    assert "REFERENCE NEVER SENT" not in serialized
    assert "SECOND REFERENCE" not in serialized
    assert "old-call-not-to-replay" not in serialized
    assert obs.trace_complete and evaluate(current, obs).gate == "pass"
    assert (obs.case_id, obs.case_revision, obs.agent_revision, obs.mode) == (
        current.id, current.revision, "immutable-live-var", "live",
    )
    assert len(obs.tool_calls) == 3  # Restored history and the follow-up did not execute old tools.
    for call in obs.tool_calls:
        assert call.id == f"var-call-{call.tool}"
        assert call.arguments == {"category": category}
        # Framework function results are serialized JSON, unlike the mock's dict results.
        assert call.result == next(message["content"] for message in results
                                   if message["tool_call_id"] == call.id)
        assert json.loads(call.result) == expected[call.tool]
        assert call.turn == 0 and call.error is None
    assert {call.tool for call in obs.tool_calls} == set(TOOLS)
    assert obs.messages == [
        {"role": "user", "content": current.turns[0].user, "turn": 0},
        {"role": "assistant", "content": answer, "turn": 0},
        {"role": "user", "content": current.turns[1].user, "turn": 1},
        {"role": "assistant", "content": "Synthetic follow-up summary", "turn": 1},
    ]
    assert obs.usage["total_token_count"] > 0 and obs.latency_ms >= 0
    assert "var-test-credential" not in obs.model_dump_json()
