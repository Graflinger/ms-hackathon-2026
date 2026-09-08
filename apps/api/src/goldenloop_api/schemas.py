from typing import Annotated, Any, Literal

from goldenloop_eval import Case
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Revision = Literal["fixed", "buggy"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CreateCase(RequestModel):
    case: Case
    reason: str | None = Field(default=None, max_length=4000)


class EditCase(CreateCase):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=4000)


class Approve(RequestModel):
    revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=4000)


class CreateRelease(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    case_ids: list[str] = Field(min_length=1, max_length=100)
    expected_revisions: dict[str, Annotated[int, Field(ge=1, strict=True)]]

    @model_validator(mode="after")
    def exact_revision_keys(self):
        if set(self.expected_revisions) != set(self.case_ids):
            raise ValueError("expected_revisions must contain exactly the selected case IDs")
        return self


class CommitImport(RequestModel):
    mapping: dict[str, str]
    duplicate_policy: Literal["new", "reject"]
    sheet: str | None = None


class CreateChat(RequestModel):
    title: str = Field(default="Synthetic conversation", min_length=1, max_length=200)
    agent_revision: Revision


class SendMessage(RequestModel):
    content: str = Field(min_length=1, max_length=16000)


class CreateFeedback(RequestModel):
    session_id: str
    turn: int = Field(ge=0, strict=True)
    tool_call_id: str | None = None
    target: Literal["answer", "tool", "missing_tool"]
    issue_type: str = Field(min_length=1, max_length=200)
    comment: str = Field(min_length=1, max_length=8000)
    correction: dict[str, Any] | None = None


class ReviewFeedback(RequestModel):
    status: Literal["accepted", "rejected", "unresolved"]
    reason: str = Field(min_length=1, max_length=4000)


class CreateRun(RequestModel):
    release_id: str
    agent_revision: Revision
    mode: Literal["mock", "live"] = "mock"
    judge: Literal["none", "azure"] = "none"
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("idempotency_key")
    @classmethod
    def safe_key(cls, value):
        if not all(c.isascii() and (c.isalnum() or c in "-_.:") for c in value):
            raise ValueError("Use an opaque alphanumeric idempotency key")
        return value
