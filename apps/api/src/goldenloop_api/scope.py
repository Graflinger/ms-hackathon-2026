"""Request-local ownership, also applied to indirect ORM lookups and aggregate queries.

Workers have no request scope: they finish already-pinned work across projects.
"""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

from .db import (
    DEMO_PROJECT,
    Agent,
    AgentRevision,
    CaseHead,
    CaseRevision,
    ChatSession,
    Event,
    Feedback,
    Import,
    MessageCommand,
    Project,
    Release,
    ReleaseCase,
    Run,
)


@dataclass(frozen=True)
class Scope:
    project_id: str
    legacy: bool
    writing: bool


current_scope: ContextVar[Scope | None] = ContextVar("project_scope", default=None)


async def request_scope(request: Request):
    project_id = request.path_params.get("project_id", DEMO_PROJECT)
    legacy = request.url.path.startswith("/api/v1/")
    route = request.scope["route"].path
    prefix = "/api/v1" if legacy else "/api/v2/projects/{project_id}"
    # Run submissions check archive state under the writer lock after matching retries.
    archive_safe = request.method == "POST" and route in {
        prefix + "/evaluation-runs/{run_id}/cancel",
        prefix + "/evaluation-runs",
    }
    writing = request.method not in {"GET", "HEAD", "OPTIONS"} and not archive_safe

    async def check_project(session):
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if writing and project.archived:
            raise HTTPException(409, "Project is archived")

    await request.app.state.db.read(check_project)
    token = current_scope.set(Scope(project_id, legacy, writing))
    try:
        yield
    finally:
        current_scope.reset(token)


async def project_scope(project_id: str, request: Request):
    # Explicit path parameter keeps shared endpoint OpenAPI accurate.
    async with asynccontextmanager(request_scope)(request):
        yield


@event.listens_for(Session, "do_orm_execute")
def scoped_queries(state):
    scope = current_scope.get()
    if scope is None or not state.is_select:
        return
    project_id = scope.project_id
    chats = select(ChatSession.id).where(ChatSession.project_id == project_id)
    runs = select(Run.id).where(Run.project_id == project_id)
    if scope.legacy:
        chats = chats.where(ChatSession.legacy_workflow.is_(True))
        runs = runs.where(Run.legacy_workflow.is_(True))
    criteria = {
        model: model.project_id == project_id
        for model in (Agent, AgentRevision, CaseHead, Import, Release, ChatSession, Run)
    }
    if scope.legacy:
        criteria[ChatSession] &= ChatSession.legacy_workflow.is_(True)
        criteria[Run] &= Run.legacy_workflow.is_(True)
    criteria.update(
        {
            CaseRevision: CaseRevision.case_id.in_(
                select(CaseHead.id).where(CaseHead.project_id == project_id)
            ),
            ReleaseCase: ReleaseCase.release_id.in_(
                select(Release.id).where(Release.project_id == project_id)
            ),
            Feedback: Feedback.session_id.in_(chats),
            MessageCommand: MessageCommand.session_id.in_(chats),
            Event: Event.stream.in_(select("chat:" + ChatSession.id).where(ChatSession.id.in_(chats)))
            | Event.stream.in_(select("run:" + Run.id).where(Run.id.in_(runs))),
        }
    )
    state.statement = state.statement.options(
        *[
            with_loader_criteria(model, condition, include_aliases=True)
            for model, condition in criteria.items()
        ]
    )


@event.listens_for(Session, "before_flush")
def scoped_inserts(session, _context, _instances):
    scope = current_scope.get()
    if scope is None:
        return
    for row in session.new:
        if isinstance(row, (CaseHead, Import, Release, ChatSession, Run)) and row.project_id is None:
            row.project_id = scope.project_id


def execution_metadata(row):
    revision = row.pinned_revision
    return {
        "project_id": row.project_id,
        "agent_id": revision.agent_id if revision else "synthetic-customer-lookup",
        "agent_name": revision.agent.name if revision else "Synthetic Customer Lookup",
        "agent_revision_id": revision.id if revision else "synthetic-" + row.agent_revision,
        "agent_revision_label": revision.label if revision else row.agent_revision,
        # A compatibility mapping is not evidence of the historical execution spec.
        "spec_hash": None if row.legacy_workflow or revision is None else revision.spec_hash,
        "legacy": row.legacy_workflow,
    }
