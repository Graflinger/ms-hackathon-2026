from typing import Any, Literal

from goldenloop_eval import Case, Evaluation, ToolCall
from pydantic import BaseModel, ConfigDict

from .schemas import Revision


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(ResponseModel):
    status: Literal["ok"]
    mode: Literal["local-synthetic"]
    sdk_version: str


class Summary(ResponseModel):
    candidates: int
    releases: int
    runs: int
    feedback: int


class CaseRecord(ResponseModel):
    case: Case
    status: Literal["candidate", "approved"]
    reviewer: str | None
    reason: str | None


class ImportRow(ResponseModel):
    row: int
    values: dict[str, Any]
    errors: list[str]


class ImportPreview(ResponseModel):
    id: str
    columns: list[str]
    rows: list[ImportRow]
    sheets: list[str]
    errors: list[str]


class ImportCommit(ResponseModel):
    cases: list[CaseRecord]
    errors: list[str]


class ReleaseRecord(ResponseModel):
    id: str
    name: str
    created_at: str
    content_hash: str
    case_count: int


class ReleaseDetail(ReleaseRecord):
    cases: list[Case]


class ChatRecord(ResponseModel):
    id: str
    title: str
    agent_revision: Revision
    created_at: str
    status: str
    trace_complete: bool
    error: str | None
    mode: Literal["mock"]


class Message(BaseModel):
    # Observable adapter messages may carry additional metadata.
    model_config = ConfigDict(extra="allow")
    id: str
    command_id: str
    role: str
    content: str
    turn: int


class ChatDetail(ChatRecord):
    messages: list[Message]
    tool_calls: list[ToolCall]


class MessageAccepted(ResponseModel):
    id: str
    session_id: str


class FeedbackRecord(ResponseModel):
    id: str
    session_id: str
    created_at: str
    candidate_id: str | None
    turn: int
    tool_call_id: str | None
    target: Literal["answer", "tool", "missing_tool"]
    issue_type: str
    comment: str
    correction: dict[str, Any] | None
    reviewer: str
    status: Literal["accepted", "rejected", "unresolved"]
    reason: str | None


class CaseResult(Evaluation):
    observation: dict[str, Any]


class RunRecord(ResponseModel):
    id: str
    release_id: str
    agent_revision: Revision
    mode: Literal["mock", "live"]
    status: str
    gate: Literal["pass", "fail", "error"] | None
    created_at: str
    release_name: str | None


class RunDetail(RunRecord):
    results: list[CaseResult]
    error: str | None
    lineage: dict[str, Any]
