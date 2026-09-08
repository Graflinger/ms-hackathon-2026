import json
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from goldenloop_eval import Case, Observation, ToolCall, evaluate
from goldenloop_eval.schema import schema_matches, schema_validator


@pytest.mark.parametrize("schema", [
    {"pattern": "(a+)+$"},
    {"patternProperties": {"(a+)+$": {}}},
    {"properties": {"x": {"pattern": "(a+)+$"}}},
    {"$defs": {"unused": {"pattern": "(a+)+$"}}},
    {"allOf": [{"items": {"patternProperties": {"(a+)+$": True}}}]},
    {"propertyNames": {"pattern": "(a+)+$"}},
    {"additionalProperties": {"format": "regex"}},
    {"format": "regex"}, {"format": "email"},
    {"$ref": "https://example.com/schema"}, {"$ref": "file:///private"},
    {"$ref": "#"}, {"$dynamicRef": "#x"}, {"$recursiveRef": "#"},
    {"$id": "https://example.com/schema"}, {"$vocabulary": {}},
    {"uniqueItems": True}, {"contains": {}}, {"if": {}, "then": {}},
    {"unevaluatedProperties": False}, {"dependentSchemas": {"x": {}}},
    {"$schema": "http://json-schema.org/draft-07/schema#"},
    {"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"},
    {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}},
    {"$ref": "#/$defs/missing"}, {"unknown-regex-extension": "(a+)+$"},
])
def test_unsafe_schema_rejected_before_jsonschema(monkeypatch, schema):
    def never_called(*args, **kwargs):
        pytest.fail("Unsafe schema reached jsonschema")
    monkeypatch.setattr(Draft202012Validator, "check_schema", never_called)
    with pytest.raises(ValueError):
        schema_validator(schema)


@pytest.mark.parametrize("kind", ["json_schema", "tool_arguments"])
def test_pathological_pattern_returns_error_promptly_in_both_evaluators(kind):
    # A subprocess deadline prevents a future regression from hanging the test process/GIL.
    script = '''
from goldenloop_eval import Case, Observation, ToolCall, evaluate
import json, sys
kind = sys.argv[1]
schema = {"type": "string", "pattern": "(a+)+$"}
config = {"schema": schema} if kind == "json_schema" else {
    "tool": "lookup", "path": "x", "operator": "schema", "value": schema}
case = Case(title="regex", turns=[{"user": "hello"}], checks=[{"kind": kind, "config": config}])
value = "a" * 10000 + "!"
obs = Observation(case_id=case.id, case_revision=1, agent_revision="fixed", trace_complete=True,
    messages=[{"role": "assistant", "turn": 0, "content": json.dumps(value)}],
    tool_calls=[ToolCall(tool="lookup", turn=0, arguments={"x": value})])
result = evaluate(case, obs)
assert result.gate == "error"
assert "Unsupported JSON schema keyword" in result.checks[0].reason
'''
    subprocess.run([sys.executable, "-c", script, kind], check=True, timeout=10, capture_output=True)


def test_safe_structural_schema_and_local_refs_remain_functional():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"id": {"type": "string", "enum": ["C-123", "C-999"]}},
        "type": "object", "required": ["id", "count"], "additionalProperties": False,
        "properties": {"id": {"$ref": "#/$defs/id"},
                       "count": {"type": "integer", "minimum": 0, "maximum": 10},
                       "tags": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 10}}},
    }
    validator = schema_validator(schema)
    assert schema_matches(validator, {"id": "C-123", "count": 2, "tags": ["test"]})
    assert not schema_matches(validator, {"id": "C-000", "count": 2})
    assert not schema_matches(validator, {"id": "C-123", "count": 20})
    assert not schema_matches(validator, {"id": "C-123", "count": 2, "extra": 0})
    assert schema_matches(schema_validator({"anyOf": [{"const": "x"}, {"type": "integer"}]}), 1)
    assert not schema_matches(schema_validator({"allOf": [True, {"not": {"const": "x"}}]}), "x")


def test_schema_depth_size_property_and_expansion_limits():
    deep = {"type": "string"}
    for _ in range(20):
        deep = {"items": deep}
    definitions = {"leaf": {"type": "integer"}}
    previous = "leaf"
    for index in range(9):
        name = str(index)
        definitions[name] = {"allOf": [{"$ref": f"#/$defs/{previous}"}] * 2}
        previous = name
    schemas = [
        deep, {"description": "a" * 65537},
        {"properties": {str(i): {} for i in range(129)}},
        {"allOf": [{"allOf": [{}] * 128}] * 3},
        {"enum": ["x" * 1024] * 100},
        {"$defs": definitions, "$ref": f"#/$defs/{previous}"},
        {"minimum": 2 ** 2048},
    ]
    for schema in schemas:
        with pytest.raises(ValueError, match="limit"):
            schema_validator(schema)


def test_oversized_instances_and_schema_errors_block_gate():
    case = Case(title="bounded", turns=[{"user": "hello"}], checks=[
        {"kind": "json_schema", "config": {"schema": {"type": "array"}}}])
    obs = Observation(case_id=case.id, case_revision=1, agent_revision="fixed", trace_complete=True,
                      messages=[{"role": "assistant", "turn": 0, "content": json.dumps([0] * 1025)}])
    assert evaluate(case, obs).gate == "error"
    case.checks[0].kind = "tool_arguments"
    case.checks[0].config = {"tool": "lookup", "path": "x", "operator": "schema", "value": {"type": "string"}}
    obs.tool_calls = [ToolCall(tool="lookup", turn=0, arguments={"x": "x" * 262145})]
    assert evaluate(case, obs).gate == "error"


def test_cycles_rejected_by_iterative_budget():
    schema = {}
    schema["items"] = schema
    with pytest.raises(ValueError, match="limit"):
        schema_validator(schema)
