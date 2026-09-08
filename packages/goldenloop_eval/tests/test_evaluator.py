import json

import pytest
from pydantic import ValidationError

from goldenloop_eval import Case, Check, Observation, ToolCall, Turn, evaluate, release_hash, sanitize


def pair(kind="content_contains", config=None, answer="customer C-123", **check_kwargs):
    case = Case(id="case", title="Synthetic", turns=[Turn(user="Look up customer C-123")],
                checks=[Check(id="check", kind=kind, config=config or {"value": "customer"}, **check_kwargs)])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="fixed", trace_complete=True,
                      messages=[{"role": "assistant", "content": answer, "turn": 0}])
    return case, obs


@pytest.mark.parametrize("kind,value,answer,gate", [
    ("content_contains", "customer", "customer C-123", "pass"),
    ("content_contains", "Customer", "customer C-123", "fail"),
    ("content_excludes", "C-999", "customer C-123", "pass"),
    ("content_excludes", "C-999", "customer C-999", "fail"),
    ("exact_match", "ok", "ok", "pass"),
    ("exact_match", "ok", "ok ", "fail"),
])
def test_content(kind, value, answer, gate):
    case, obs = pair(kind, {"value": value}, answer)
    result = evaluate(case, obs)
    assert result.gate == gate
    assert result.checks[0].evaluator_version
    assert json.loads(result.model_dump_json())["case_id"] == case.id


@pytest.mark.parametrize("answer,gate", [('{"a":1}', "pass"), ('{"a":"1"}', "fail"),
                                         ("not json", "fail"), ('{"a":NaN}', "fail")])
def test_json_schema(answer, gate):
    case, obs = pair("json_schema", {"schema": {"type": "object", "required": ["a"],
                     "properties": {"a": {"type": "integer"}}}}, answer)
    assert evaluate(case, obs).gate == gate


@pytest.mark.parametrize("schema", [
    {"type": "nonsense"}, {"$ref": "https://localhost/private"},
    {"$defs": {"x": {"$ref": "file:///secret"}}}, {"$schema": "https://invalid/dialect"},
])
def test_bad_schema_is_error(schema):
    assert evaluate(*pair("json_schema", {"schema": schema}, "{}")).gate == "error"


def test_local_schema_reference():
    schema = {"$defs": {"x": {"type": "integer"}}, "$ref": "#/$defs/x"}
    assert evaluate(*pair("json_schema", {"schema": schema}, "1")).gate == "pass"


@pytest.mark.parametrize("kind,config", [
    ("unknown", {}), ("content_contains", {}), ("content_contains", {"value": ""}),
    ("content_contains", {"value": "x", "typo": True}),
    ("tool_required", {"tool": "lookup", "min": 0}),
    ("tool_required", {"tool": "lookup", "min": 2, "max": 1}),
    ("tool_required", {"tool": "lookup", "min": True}),
    ("tool_arguments", {"tool": "lookup", "path": "x", "operator": "eval", "value": "x"}),
    ("tool_arguments", {"tool": "lookup", "path": "x", "operator": "range", "value": {"min": 2, "max": 1}}),
    ("tool_arguments", {"tool": "lookup", "path": "x", "operator": "type", "value": "str"}),
    ("tool_order", {"tools": []}), ("judge", {"rubric": "accuracy", "threshold": float("nan")}),
])
def test_bad_checks_error(kind, config):
    case, obs = pair(kind)
    case.checks[0].config = config
    assert evaluate(case, obs).gate == "error"


@pytest.mark.parametrize("mutation", [
    lambda o: setattr(o, "messages", []),
    lambda o: setattr(o, "messages", [{"role": "user", "content": "customer", "turn": 0}]),
    lambda o: setattr(o, "messages", [{"role": "assistant", "content": "customer", "turn": True}]),
    lambda o: setattr(o, "messages", [{"role": "assistant", "content": "customer", "turn": 1}]),
    lambda o: setattr(o, "messages", [{"role": "assistant", "content": " ", "turn": 0}]),
    lambda o: setattr(o, "case_id", "other"),
    lambda o: setattr(o, "case_revision", 2),
    lambda o: setattr(o, "error", "timeout"),
    lambda o: setattr(o, "tool_calls", [ToolCall(tool="lookup", turn=0, error="bad")]),
    lambda o: setattr(o, "tool_calls", [ToolCall(tool="lookup", turn=2)]),
    lambda o: setattr(o, "tool_calls", [ToolCall(id="same", tool="a", turn=0)] * 2),
])
def test_incomplete_execution_never_passes(mutation):
    case, obs = pair()
    mutation(obs)
    result = evaluate(case, obs)
    assert result.gate == "error"
    assert result.checks[-1].id == "__execution__"


def test_empty_expectations_and_only_optional_are_not_golden():
    case, obs = pair(required=False)
    assert evaluate(case, obs).gate == "error"
    case.checks = []
    assert evaluate(case, obs).gate == "error"


def test_optional_error_does_not_override_required_pass():
    case, obs = pair()
    case.checks.append(Check(kind="judge", required=False, config={"rubric": "accuracy", "threshold": 0.8}))
    result = evaluate(case, obs)
    assert result.gate == "pass"
    assert result.checks[1].status == "error"


@pytest.mark.parametrize("kind,config", [
    ("tool_required", {"tool": "lookup"}), ("tool_forbidden", {"tool": "write"}),
    ("tool_arguments", {"tool": "lookup", "path": "x", "operator": "equals", "value": 1}),
    ("tool_order", {"tools": ["lookup"]}),
])
def test_missing_trace_fails_closed(kind, config):
    case, obs = pair(kind, config)
    obs.trace_complete = False
    assert evaluate(case, obs).gate == "error"


@pytest.mark.parametrize("count,gate", [(0, "fail"), (1, "pass"), (2, "pass"), (3, "fail")])
def test_tool_counts_and_alternatives(count, gate):
    case, obs = pair("tool_required", {"tool": "lookup", "alternatives": ["cached_lookup"], "min": 1, "max": 2})
    obs.tool_calls = [ToolCall(tool="cached_lookup", turn=0) for _ in range(count)]
    obs.tool_calls.append(ToolCall(tool="irrelevant", turn=0))
    assert evaluate(case, obs).gate == gate


@pytest.mark.parametrize("count,gate", [(0, "pass"), (1, "fail")])
def test_forbidden(count, gate):
    case, obs = pair("tool_forbidden", {"tool": "write", "max": 0})
    obs.tool_calls = [ToolCall(tool="write", turn=0) for _ in range(count)]
    assert evaluate(case, obs).gate == gate


@pytest.mark.parametrize("operator,value,actual,gate", [
    ("equals", "C-123", "C-123", "pass"), ("equals", "C-123", "C-999", "fail"),
    ("equals", 1, True, "fail"), ("subset", {"a": 1}, {"a": 1, "b": 2}, "pass"),
    ("subset", [1, 1], [1], "fail"), ("subset", [{"a": 1}], [{"a": 1, "b": 2}], "pass"),
    ("type", "integer", 1, "pass"), ("type", "integer", True, "fail"),
    ("type", "object", {}, "pass"), ("range", {"min": 1, "max": 3}, 3, "pass"),
    ("range", {"min": 1}, 0, "fail"), ("range", {"min": 0}, True, "fail"),
    ("schema", {"type": "string", "enum": ["C-123", "C-999"]}, "C-123", "pass"),
])
def test_argument_operators(operator, value, actual, gate):
    case, obs = pair("tool_arguments", {"tool": "lookup", "path": "nested.0.x", "operator": operator, "value": value})
    obs.tool_calls = [ToolCall(tool="lookup", turn=0, arguments={"nested": [{"x": actual}]})]
    assert evaluate(case, obs).gate == gate


def test_argument_all_repetitions_must_match_and_missing_path_fails():
    case, obs = pair("tool_arguments", {"tool": "lookup", "path": "/a~1b/~0x", "operator": "equals", "value": "C-123"})
    assert evaluate(case, obs).gate == "fail"
    obs.tool_calls = [ToolCall(tool="lookup", turn=0, arguments={"a/b": {"~x": "C-123"}})]
    assert evaluate(case, obs).gate == "pass"
    obs.tool_calls.append(ToolCall(tool="lookup", turn=0, arguments={}))
    assert evaluate(case, obs).gate == "fail"


@pytest.mark.parametrize("names,gate", [
    (["a", "x", "b", "a"], "pass"), (["a", "b"], "fail"),
    (["b", "a", "a"], "fail"), ([], "fail"),
])
def test_tool_order_is_subsequence(names, gate):
    case, obs = pair("tool_order", {"tools": ["a", "b", "a"]})
    obs.tool_calls = [ToolCall(tool=name, turn=0) for name in names]
    assert evaluate(case, obs).gate == gate


def test_multi_turn_scope_and_absent_turn():
    case, obs = pair(turn=0)
    case.turns.append(Turn(user="follow up", reference_answer="customer"))
    assert evaluate(case, obs).gate == "error"
    obs.messages.append({"role": "assistant", "content": "other", "turn": 1})
    assert evaluate(case, obs).gate == "pass"
    case.checks[0].turn = None
    assert evaluate(case, obs).gate == "fail"
    case.checks[0].turn = 2
    assert evaluate(case, obs).gate == "error"


def verdict(**updates):
    return {"score": 0.8, "reason": "Matches reference", "evidence": ["C-123"],
            "insufficient_evidence": False, "provider": "unit-fixture", "model": "static",
            "prompt_version": "test-v1", "mode": "mock", **updates}


def test_required_judge_missing_and_threshold():
    case, obs = pair("judge", {"rubric": "accuracy", "threshold": 0.8})
    assert evaluate(case, obs).gate == "error"
    result = evaluate(case, obs, judge=lambda *args: verdict())
    assert result.gate == "pass"
    assert result.checks[0].evidence["mode"] == "mock"
    assert evaluate(case, obs, judge=lambda *args: verdict(score=0.79)).gate == "fail"


@pytest.mark.parametrize("output", [{}, {"score": 1}, verdict(score="0.9"), verdict(score=True),
                                   verdict(score=float("nan")), verdict(score=1.1),
                                   verdict(insufficient_evidence=True), verdict(extra="ignored?")])
def test_malformed_or_insufficient_judge_never_passes(output):
    assert evaluate(*pair("judge", {"rubric": "accuracy", "threshold": 0.8}), judge=lambda *a: output).gate == "error"


def test_judge_inputs_and_errors_are_sanitized():
    case, obs = pair("judge", {"rubric": "accuracy", "threshold": 0.8}, "api_key=secret-value customer")
    def judge(c, o, check):
        assert "secret-value" not in o.model_dump_json()
        raise ValueError("unrecognized raw credential value 1234")
    result = evaluate(case, obs, judge)
    assert result.gate == "error"
    assert "1234" not in result.model_dump_json()


def test_models_roundtrip_defaults_and_validation():
    case, obs = pair()
    assert Case.model_validate(case.model_dump(mode="json")) == case
    assert Observation.model_validate(obs.model_dump(mode="json")) == obs
    other = Case(title="other", turns=[{"user": "hello"}])
    case.tags.append("one")
    assert other.tags == []
    with pytest.raises(ValidationError):
        Case(title="empty", turns=[])
    with pytest.raises(ValidationError):
        Check(kind="x", turn=-1)
    with pytest.raises(ValidationError):
        Case(title="duplicates", turns=[{"user": "x"}], checks=[{"id": "a", "kind": "x"}] * 2)


def test_hash_canonical_and_revision_sensitive():
    a, _ = pair()
    b = a.model_copy(deep=True, update={"id": "other"})
    assert len(release_hash([a])) == 64
    assert release_hash([a, b]) == release_hash([b, a])
    c = Case.model_validate(json.loads(json.dumps(a.model_dump(mode="json"), sort_keys=True)))
    assert release_hash([a]) == release_hash([c])
    c.revision += 1
    assert release_hash([a]) != release_hash([c])
    with pytest.raises(ValueError):
        release_hash([a, a])


def test_sanitize_non_mutating_nested_and_explicit_secrets():
    raw = {"api_key": "x", "nested": [{"message": "Bearer abc sk-abcdefgh person@example.com opaque"}], "customer_id": "C-123"}
    safe = sanitize(raw, secrets=("opaque",))
    assert raw["api_key"] == "x"
    assert safe["api_key"] == "[REDACTED]"
    assert "opaque" not in json.dumps(safe)
    assert "person@example.com" not in json.dumps(safe)
    assert safe["customer_id"] == "C-123"


@pytest.mark.parametrize("blank", ["", " \t\n"])
@pytest.mark.parametrize("kind,config", [
    ("content_excludes", {"value": "forbidden"}),
    ("tool_required", {"tool": "lookup"}),
    ("tool_forbidden", {"tool": "write"}),
])
def test_last_blank_answer_is_incomplete_even_after_nonblank(kind, config, blank):
    case, obs = pair(kind, config)
    obs.tool_calls = [ToolCall(tool="lookup", turn=0)]
    obs.messages.append({"role": "assistant", "content": blank, "turn": 0})
    result = evaluate(case, obs)
    assert result.gate == "error"
    assert result.checks[0].status == "skipped"
    assert "Missing assistant answer for turn 0" in result.checks[-1].reason


def test_only_last_answer_is_scored_and_each_turn_is_checked():
    case, obs = pair("exact_match", {"value": "final"}, answer="")
    obs.messages.append({"role": "assistant", "content": "final", "turn": 0})
    assert evaluate(case, obs).gate == "pass"
    case.turns.append(Turn(user="follow up"))
    obs.messages.extend([
        {"role": "assistant", "content": "final", "turn": 1},
        {"role": "assistant", "content": "", "turn": 1},
    ])
    assert evaluate(case, obs).gate == "error"


def test_evidence_redaction_collision_is_evaluator_error():
    case, obs = pair("tool_required", {"tool": "lookup"})
    obs.tool_calls = [ToolCall(tool="lookup", turn=0, result={"a@example.com": 1, "b@example.com": 2})]
    result = evaluate(case, obs)
    assert result.gate == "error"
    assert result.checks[0].evidence is None
    assert result.checks[0].reason == "Redaction would produce duplicate dictionary keys"
    assert "@example.com" not in result.model_dump_json()
