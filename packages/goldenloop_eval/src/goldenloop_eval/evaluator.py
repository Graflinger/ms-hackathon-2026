import hashlib
import json
import math
from collections.abc import Callable
from typing import Any

from pydantic import ConfigDict, Field

from .models import Case, Check, CheckResult, Evaluation, JudgeVerdict, Model, Observation
from .privacy import sanitize
from .schema import schema_matches, schema_validator

Judge = Callable[[Case, Observation, Check], JudgeVerdict | dict[str, Any]]


class Config(Model):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ContentConfig(Config):
    value: str = Field(min_length=1)


class SchemaConfig(Config):
    json_schema: dict[str, Any] = Field(alias="schema")


class ToolConfig(Config):
    tool: str = Field(min_length=1)
    alternatives: list[str] = Field(default_factory=list)
    min: int = Field(default=1, ge=0)
    max: int | None = Field(default=None, ge=0)


class ArgumentConfig(Config):
    tool: str = Field(min_length=1)
    alternatives: list[str] = Field(default_factory=list)
    path: str
    operator: str
    value: Any


class OrderConfig(Config):
    tools: list[str] = Field(min_length=1)


class JudgeConfig(Config):
    rubric: str = Field(min_length=1)
    threshold: float = Field(ge=0, le=1)


def release_hash(cases: list[Case]) -> str:
    """SHA256 of UTF-8 canonical JSON, sorted by case ID/revision.

    Object keys and case selection order are immaterial; turn/check order is not.
    Hashing does not sanitize, assign approval, or alter the supplied cases.
    """
    keys = [(case.id, case.revision) for case in cases]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate case revisions")
    payload = [case.model_dump(mode="json") for case in sorted(cases, key=lambda c: (c.id, c.revision))]
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")).hexdigest()


def _last_answer(observation: Observation, turn: int) -> Any:
    return next((m.get("content") for m in reversed(observation.messages)
                 if m.get("role") == "assistant" and m.get("turn") == turn), None)


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            _equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _equal(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def _subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _subset(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        remaining = list(actual)
        for item in expected:
            match = next((i for i, value in enumerate(remaining) if _subset(value, item)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    return _equal(actual, expected)


def _path(arguments: dict, path: str) -> Any:
    if path == "":
        return arguments
    parts = ([p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
             if path.startswith("/") else path.split("."))
    value: Any = arguments
    for part in parts:
        if isinstance(value, list) and part.isdecimal():
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise KeyError(path)
    return value


def validate_case_checks(case: Case, *, judge_available: bool) -> None:
    """Validate the complete check configuration without execution or provider calls."""
    if not any(check.required for check in case.checks):
        raise ValueError("Case has no required actionable expectations")
    for check in case.checks:
        _check_config(case, check)
        if check.required and check.kind == "judge" and not judge_available:
            raise ValueError("Required judge provider is not configured")


def _check_config(case: Case, check: Check):
    if check.turn is not None and check.turn >= len(case.turns):
        raise ValueError("Check turn is outside the case")
    kind = check.kind
    if kind in {"content_contains", "content_excludes", "exact_match"}:
        return ContentConfig.model_validate(check.config)
    if kind == "json_schema":
        config = SchemaConfig.model_validate(check.config)
        schema_validator(config.json_schema)
    elif kind in {"tool_required", "tool_forbidden"}:
        config = ToolConfig.model_validate(check.config)
        if kind == "tool_required" and config.max is not None and config.max < config.min:
            raise ValueError("Tool maximum must be at least minimum")
        if kind == "tool_required" and config.min == 0:
            raise ValueError("tool_required minimum must be positive")
        if kind == "tool_forbidden" and (config.min != 1 or config.max not in {None, 0}):
            raise ValueError("tool_forbidden does not accept nonzero occurrence bounds")
    elif kind == "tool_arguments":
        config = ArgumentConfig.model_validate(check.config)
        if config.operator == "type":
            if config.value not in ("string", "integer", "number", "boolean", "null", "array", "object"):
                raise ValueError("Unsupported JSON type")
        elif config.operator == "schema":
            if not isinstance(config.value, dict):
                raise ValueError("schema assertion value must be an object")
            schema_validator(config.value)
        elif config.operator == "range":
            bounds = config.value
            if (not isinstance(bounds, dict) or not bounds or set(bounds) - {"min", "max"}
                or any(type(v) not in {int, float} or not math.isfinite(v) for v in bounds.values())
                or bounds.get("min", -math.inf) > bounds.get("max", math.inf)):
                raise ValueError("range value must contain valid inclusive min/max bounds")
        elif config.operator not in {"equals", "subset"}:
            raise ValueError("Unsupported argument operator")
    elif kind == "tool_order":
        config = OrderConfig.model_validate(check.config)
        if any(not name for name in config.tools):
            raise ValueError("Tool order names cannot be empty")
    elif kind == "judge":
        config = JudgeConfig.model_validate(check.config)
    else:
        raise ValueError("Unsupported check kind")
    return config


def _run_check(case: Case, observation: Observation, check: Check, judge: Judge | None):
    config = _check_config(case, check)
    turns = list(range(len(case.turns))) if check.turn is None else [check.turn]
    # Unscoped content assertions apply independently to every scripted answer.
    answers = [_last_answer(observation, turn) for turn in turns]
    kind = check.kind
    evidence: Any = {"turns": turns, "answers": answers}
    if kind in {"content_contains", "content_excludes", "exact_match"}:
        if kind == "content_contains":
            passed = all(config.value in answer for answer in answers)
        elif kind == "content_excludes":
            passed = all(config.value not in answer for answer in answers)
        else:
            passed = all(config.value == answer for answer in answers)
    elif kind == "json_schema":
        validator = schema_validator(config.json_schema)
        if any(len(answer) > 262144 for answer in answers):
            raise ValueError("JSON answer exceeds size limit")
        try:
            values = [json.loads(answer, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                      for answer in answers]
        except ValueError:
            return "fail", "Answer is not valid JSON", evidence, None
        passed = all(schema_matches(validator, value) for value in values)
    elif kind.startswith("tool_"):
        if not observation.trace_complete:
            raise ValueError("Required tool telemetry is incomplete")
        calls = [call for call in observation.tool_calls if call.turn in turns]
        if kind in {"tool_required", "tool_forbidden"}:
            names = {config.tool, *config.alternatives}
            matched = [call for call in calls if call.tool in names]
            passed = (len(matched) == 0 if kind == "tool_forbidden" else
                      len(matched) >= config.min and (config.max is None or len(matched) <= config.max))
            evidence = {"count": len(matched), "calls": [c.model_dump(mode="json") for c in matched]}
        elif kind == "tool_arguments":
            matched = [call for call in calls if call.tool in {config.tool, *config.alternatives}]
            operator = config.operator
            validator = None
            if operator == "type":
                validator = schema_validator({"type": config.value})
            elif operator == "schema":
                validator = schema_validator(config.value)
            elif operator == "range":
                bounds = config.value
            outcomes = []
            for call in matched:
                try:
                    actual = _path(call.arguments, config.path)
                except (KeyError, IndexError):
                    outcomes.append(False)
                    continue
                if operator == "equals":
                    outcomes.append(_equal(actual, config.value))
                elif operator == "subset":
                    outcomes.append(_subset(actual, config.value))
                elif operator == "range":
                    outcomes.append(type(actual) in {int, float} and math.isfinite(actual)
                                    and bounds.get("min", -math.inf) <= actual <= bounds.get("max", math.inf))
                else:
                    outcomes.append(schema_matches(validator, actual))
            # Every matching repeated call must satisfy the constraint; absence fails.
            passed = bool(matched) and all(outcomes)
            evidence = {"calls": [c.model_dump(mode="json") for c in matched], "matches": outcomes}
        elif kind == "tool_order":
            # Ordered subsequence: unrelated/alternative intermediate calls are allowed.
            cursor = 0
            for call in calls:
                if call.tool == config.tools[cursor]:
                    cursor += 1
                    if cursor == len(config.tools):
                        break
            passed = cursor == len(config.tools)
            evidence = {"observed": [c.tool for c in calls], "expected": config.tools}
        else:
            raise ValueError("Unsupported check kind")
    elif kind == "judge":
        if judge is None:
            raise ValueError("Judge provider is not configured")
        safe_case = Case.model_validate(sanitize(case.model_dump(mode="json")))
        safe_observation = Observation.model_validate(sanitize(observation.model_dump(mode="json")))
        safe_check = Check.model_validate(sanitize(check.model_dump(mode="json")))
        try:
            verdict = JudgeVerdict.model_validate(judge(safe_case, safe_observation, safe_check))
        except Exception:
            raise RuntimeError("Judge provider failed or returned malformed output") from None
        if verdict.insufficient_evidence:
            return "error", "Judge reported insufficient evidence", verdict.model_dump(mode="json"), verdict.score
        return ("pass" if verdict.score >= config.threshold else "fail", verdict.reason,
                verdict.model_dump(mode="json"), verdict.score)
    else:
        raise ValueError("Unsupported check kind")
    return "pass" if passed else "fail", "Constraint satisfied" if passed else "Constraint not satisfied", evidence, None


def evaluate(case: Case, observation: Observation, judge: Judge | None = None, *,
             secrets: tuple[str, ...] = ()) -> Evaluation:
    """Score an observation without invoking the agent; required checks fail closed."""
    secrets = (*secrets, *getattr(judge, "secrets", ()))
    if secrets:
        try:
            case = Case.model_validate(sanitize(case.model_dump(mode="json"), secrets=secrets))
            observation = Observation.model_validate(sanitize(observation.model_dump(mode="json"), secrets=secrets))
        except ValueError:
            return Evaluation(case_id=sanitize(case.id, secrets=secrets), case_revision=case.revision,
                              agent_revision=sanitize(observation.agent_revision, secrets=secrets),
                              mode=observation.mode, gate="error", checks=[CheckResult(
                                  id="__execution__", kind="execution", status="error", reason="Unsafe evaluation data")])
    execution_errors = []
    if (case.id, case.revision) != (observation.case_id, observation.case_revision):
        execution_errors.append("Observation case identity/revision mismatch")
    if observation.error is not None:
        execution_errors.append("Agent execution error (see sanitized observation)")
    if any(call.error is not None for call in observation.tool_calls):
        execution_errors.append("Tool execution error")
    if not any(check.required for check in case.checks):
        execution_errors.append("Case has no required actionable expectations")
    call_ids = [call.id for call in observation.tool_calls]
    if len(call_ids) != len(set(call_ids)):
        execution_errors.append("Duplicate tool call IDs")
    if any(call.turn >= len(case.turns) for call in observation.tool_calls):
        execution_errors.append("Tool call turn is outside the case")
    for message in observation.messages:
        if (message.get("role") not in {"user", "assistant", "tool", "system", "developer"}
            or not isinstance(message.get("content"), str)
            or type(message.get("turn")) is not int
            or not 0 <= message["turn"] < len(case.turns)):
            execution_errors.append("Malformed observation message")
            break
    for turn in range(len(case.turns)):
        answer = _last_answer(observation, turn)
        if not isinstance(answer, str) or not answer.strip():
            execution_errors.append(f"Missing assistant answer for turn {turn}")
    results = []
    for check in case.checks:
        if execution_errors:
            status, reason, evidence, score = "skipped", "Execution is incomplete", None, None
        else:
            try:
                status, reason, evidence, score = _run_check(case, observation, check, judge)
                evidence = sanitize(evidence, secrets=secrets)
            except Exception as exc:
                # Never persist provider exception text: it can include URLs/credentials.
                status, reason, evidence, score = "error", f"Invalid check or evaluator failure ({type(exc).__name__})", None, None
                if isinstance(exc, ValueError) and type(exc) is ValueError:
                    reason = str(exc)
        results.append(CheckResult(id=check.id, kind=check.kind, status=status,
                                    reason=sanitize(reason, secrets=secrets), evidence=evidence, score=score))
    required = [result for check, result in zip(case.checks, results) if check.required]
    if execution_errors:
        results.append(CheckResult(id="__execution__", kind="execution", status="error",
                                   reason="; ".join(execution_errors)))
    gate = ("error" if execution_errors or any(r.status in {"error", "skipped"} for r in required)
            else "fail" if any(r.status == "fail" for r in required) else "pass")
    return Evaluation(case_id=case.id, case_revision=case.revision, checks=results, gate=gate,
                      agent_revision=observation.agent_revision, mode=observation.mode)
