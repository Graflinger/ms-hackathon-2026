from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

SDK_VERSION = "0.2.0"
Mode = Literal["mock", "live", "recorded"]


def new_id() -> str:
    return str(uuid4())


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Turn(Model):
    user: str = Field(min_length=1)
    reference_answer: str | None = None


class Check(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    kind: str
    required: bool = True
    turn: int | None = Field(default=None, ge=0, strict=True)
    config: dict[str, Any] = Field(default_factory=dict)


class Case(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    revision: int = Field(default=1, ge=1, strict=True)
    title: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    context: str = ""
    turns: list[Turn] = Field(min_length=1)
    checks: list[Check] = Field(default_factory=list)
    fixture_version: str = "synthetic-v1"

    @model_validator(mode="after")
    def unique_checks(self):
        ids = [check.id for check in self.checks]
        if len(ids) != len(set(ids)) or "__execution__" in ids:
            raise ValueError("Check IDs must be unique and cannot be __execution__")
        return self


class ToolCall(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    turn: int = Field(ge=0, strict=True)
    latency_ms: float | None = Field(default=None, ge=0)
    parent_id: str | None = None


class Observation(Model):
    case_id: str
    case_revision: int = Field(ge=1, strict=True)
    agent_revision: str = Field(min_length=1)
    mode: Mode = "mock"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    trace_complete: bool = False
    latency_ms: float | None = Field(default=None, ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class CheckResult(Model):
    id: str
    kind: str
    status: Literal["pass", "fail", "error", "skipped"]
    reason: str
    evidence: Any = None
    score: float | None = None
    evaluator_version: str = SDK_VERSION


class Evaluation(Model):
    case_id: str
    case_revision: int
    checks: list[CheckResult]
    gate: Literal["pass", "fail", "error"]
    agent_revision: str
    mode: Mode


class JudgeVerdict(Model):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    evidence: Any
    insufficient_evidence: bool
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    mode: Literal["mock", "live"]
    settings: dict[str, Any] = Field(default_factory=dict)


class BundleManifest(Model):
    schema_version: Literal["1"]
    sdk_version: Literal["0.1.0", "0.2.0"]
    release_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases_file: Literal["cases.json"]
    agent_revision: str = Field(min_length=1)
    mode: Mode
