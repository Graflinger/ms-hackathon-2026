"""Project registry and v2 execution. Shared curation routes use scoped ORM sessions."""

import asyncio
import importlib.util
import json
import os
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from fastapi.routing import APIRoute
from goldenloop_demo_agent import resolve_binding
from goldenloop_eval import SDK_VERSION, AgentConnection, AgentSpec, Case, agent_spec_hash
from goldenloop_eval.agents import TRUSTED_AGENT_ARTIFACT, resource_origin
from pydantic import Field, field_validator
from sqlalchemy import func, select

from . import responses
from .config import clean
from .db import (
    Agent,
    AgentRevision,
    ChatSession,
    Project,
    Release,
    ReleaseCase,
    Run,
    add_event,
    pending_count,
    require_active_project,
)
from .exports import export_bundle_v2
from .judging import judge_lineage
from .schemas import CreateRun, RequestModel
from .scope import project_scope, request_scope
from .security import LOCAL_IDENTITY, require


class CreateMetadata(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value


class PatchMetadata(RequestModel):
    name: str = Field(default=None, min_length=1, max_length=200)
    description: str = Field(default=None, max_length=4000)
    archived: bool = Field(default=None, strict=True)

    _nonblank = field_validator("name")(CreateMetadata.nonblank.__func__)


class ProjectRecord(responses.ResponseModel):
    id: str
    name: str
    description: str
    archived: bool
    created_at: str


class AgentRecord(ProjectRecord):
    project_id: str


class RevisionRecord(responses.ResponseModel):
    id: str
    agent_id: str
    project_id: str
    number: int
    label: str
    spec: AgentSpec
    spec_hash: str
    created_at: str
    legacy: bool = False
    spec_provenance: Literal["mapping_only", "registered"]


class CreateRevision(RequestModel):
    label: str = Field(min_length=1, max_length=200)
    spec: AgentSpec

    _nonblank = field_validator("label")(CreateMetadata.nonblank.__func__)


class CreateChatV2(RequestModel):
    title: str = Field(default="Synthetic conversation", min_length=1, max_length=200)
    agent_revision_id: str = Field(min_length=1, max_length=200)


class CreateRunV2(RequestModel):
    release_id: str
    agent_revision_id: str
    mode: Literal["mock", "live"]
    judge: Literal["none", "azure"] = "none"
    idempotency_key: str = Field(min_length=1, max_length=200)

    _safe_key = field_validator("idempotency_key")(CreateRun.safe_key.__func__)


class BindingRecord(responses.ResponseModel):
    id: str
    endpoint: str
    auth: Literal["api_key", "azure_cli"]
    configured: bool


def record(row, schema):
    return {field: getattr(row, field) for field in schema.model_fields}


def bindings_config():
    try:
        raw = os.environ.get("GOLDENLOOP_CONNECTION_BINDINGS", "{}")
        if len(raw.encode("utf-8")) > 1024 * 1024:
            raise ValueError()
        bindings = json.loads(raw)
        if not isinstance(bindings, dict):
            raise TypeError()
        return bindings
    except (ValueError, TypeError):
        raise HTTPException(409, "Server connection bindings are invalid") from None


def approved_binding(connection, project_id, bindings):
    """Authorize metadata without requiring an installed credential at registration."""
    entry = bindings.get(connection.binding)
    try:
        if (
            not isinstance(entry, dict)
            or set(entry) - {"endpoint", "auth", "key_env", "projects"}
            or not isinstance(entry.get("projects"), list)
            or not all(isinstance(p, str) for p in entry["projects"])
            or project_id not in entry["projects"]
            or entry.get("auth") != connection.auth
            or resource_origin(entry.get("endpoint", "")) != connection.endpoint
        ):
            raise ValueError()
        key_env = entry.get("key_env")
        if connection.auth == "azure_cli":
            if key_env is not None:
                raise ValueError()
        elif not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            409, "Binding does not authorize this project, endpoint and authentication"
        ) from None


def validate_capabilities(spec, cases, mode):
    if spec.artifact != TRUSTED_AGENT_ARTIFACT:
        raise HTTPException(409, "Unsupported pinned agent artifact")
    if mode not in spec.modes:
        raise HTTPException(409, "Revision does not support the selected mode")
    errors = []
    for case in cases:
        reasons = []
        if case.fixture_version != spec.fixture_version:
            reasons.append("incompatible fixture")
        if len(case.turns) > 1 and not spec.supports_multi_turn:
            reasons.append("multi-turn capability required")
        if not spec.trace_available and any(c.required and c.kind.startswith("tool_") for c in case.checks):
            reasons.append("tool trace capability required")
        if reasons:
            errors.append({"case_id": case.id, "reasons": reasons})
    if errors:
        raise HTTPException(409, {"message": "Release incompatible with revision", "cases": errors})


def execution_key(spec, project_id, mode, settings):
    if mode == "mock":
        return None
    if not settings.allow_live or importlib.util.find_spec("agent_framework") is None:
        raise HTTPException(409, "Live mode requires synthetic-data opt-in and live adapter dependencies")
    bindings = bindings_config()
    approved_binding(spec.connection, project_id, bindings)
    try:
        return resolve_binding(spec.connection, project_id, bindings)
    except ValueError:
        raise HTTPException(409, "Connection binding credential is unavailable") from None


async def selected_revision(session, revision_id, active=False):
    revision = await session.get(AgentRevision, revision_id)
    if revision is None:
        raise HTTPException(404, "Agent revision not found")
    if active and revision.agent.archived:
        raise HTTPException(409, "Agent is archived")
    return revision


async def release_cases(session, release_id):
    return [
        Case.model_validate(row.payload)
        for row in await session.scalars(
            select(ReleaseCase).where(ReleaseCase.release_id == release_id).order_by(ReleaseCase.case_id)
        )
    ]


def install_v2(app, legacy_router, db, settings):
    from .main import chat_record, get_or_404, run_record

    registry = APIRouter(prefix="/api/v2", dependencies=[Depends(require("read"))])
    review = [Depends(require("review"))]
    execute = [Depends(require("execute"))]

    @registry.get("/projects", response_model=list[ProjectRecord])
    async def projects():
        async with db.sessions() as session:
            return [
                record(row, ProjectRecord)
                for row in await session.scalars(select(Project).order_by(Project.created_at, Project.id))
            ]

    @registry.post("/projects", dependencies=review, status_code=201, response_model=ProjectRecord)
    async def create_project(body: CreateMetadata):
        async with db.write() as session:
            row = Project(**clean(body.model_dump()))
            session.add(row)
            await session.flush()
            return record(row, ProjectRecord)

    @registry.get("/projects/{project_id}", response_model=ProjectRecord)
    async def project(project_id: str):
        async with db.sessions() as session:
            return record(await get_or_404(session, Project, project_id), ProjectRecord)

    @registry.patch("/projects/{project_id}", dependencies=review, response_model=ProjectRecord)
    async def patch_project(project_id: str, body: PatchMetadata):
        async with db.write() as session:
            row = await get_or_404(session, Project, project_id)
            patch = clean(body.model_dump(exclude_unset=True))
            if row.archived and any(key != "archived" for key in patch):
                raise HTTPException(409, "Unarchive the project before editing metadata")
            for key, value in patch.items():
                setattr(row, key, value)
            return record(row, ProjectRecord)

    scoped = APIRouter(
        prefix="/api/v2/projects/{project_id}",
        dependencies=[Depends(require("read")), Depends(project_scope)],
    )

    @scoped.get("/agents", response_model=list[AgentRecord])
    async def agents(project_id: str):
        async with db.sessions() as session:
            return [
                record(row, AgentRecord)
                for row in await session.scalars(select(Agent).order_by(Agent.created_at, Agent.id))
            ]

    @scoped.post("/agents", dependencies=review, status_code=201, response_model=AgentRecord)
    async def create_agent(project_id: str, body: CreateMetadata):
        async with db.write() as session:
            row = Agent(project_id=project_id, **clean(body.model_dump()))
            session.add(row)
            await session.flush()
            return record(row, AgentRecord)

    @scoped.get("/agents/{agent_id}", response_model=AgentRecord)
    async def agent(project_id: str, agent_id: str):
        async with db.sessions() as session:
            return record(await get_or_404(session, Agent, agent_id), AgentRecord)

    @scoped.patch("/agents/{agent_id}", dependencies=review, response_model=AgentRecord)
    async def patch_agent(project_id: str, agent_id: str, body: PatchMetadata):
        async with db.write() as session:
            row = await get_or_404(session, Agent, agent_id)
            patch = clean(body.model_dump(exclude_unset=True))
            if row.archived and any(key != "archived" for key in patch):
                raise HTTPException(409, "Unarchive the agent before editing metadata")
            for key, value in patch.items():
                setattr(row, key, value)
            return record(row, AgentRecord)

    @scoped.get("/agents/{agent_id}/revisions", response_model=list[RevisionRecord])
    async def revisions(project_id: str, agent_id: str):
        async with db.sessions() as session:
            await get_or_404(session, Agent, agent_id)
            return [
                record(row, RevisionRecord)
                for row in await session.scalars(
                    select(AgentRevision)
                    .where(AgentRevision.agent_id == agent_id)
                    .order_by(AgentRevision.number)
                )
            ]

    @scoped.post(
        "/agents/{agent_id}/revisions", dependencies=review, status_code=201, response_model=RevisionRecord
    )
    async def create_revision(project_id: str, agent_id: str, body: CreateRevision):
        async with db.write() as session:
            agent = await get_or_404(session, Agent, agent_id)
            if agent.archived:
                raise HTTPException(409, "Agent is archived")
            if body.spec.artifact != TRUSTED_AGENT_ARTIFACT:
                raise HTTPException(409, "Unsupported pinned agent artifact")
            if clean(body.spec.model_dump(mode="json")) != body.spec.model_dump(mode="json"):
                raise HTTPException(422, "Agent specification contains sensitive values")
            if body.spec.connection is not None:
                approved_binding(body.spec.connection, project_id, bindings_config())
            number = (
                await session.scalar(
                    select(func.max(AgentRevision.number)).where(AgentRevision.agent_id == agent_id)
                )
                or 0
            ) + 1
            row = AgentRevision(
                project_id=project_id,
                agent_id=agent_id,
                number=number,
                label=clean(body.label),
                spec=body.spec.model_dump(mode="json"),
                spec_hash=agent_spec_hash(body.spec),
                legacy=False,
            )
            session.add(row)
            await session.flush()
            return record(row, RevisionRecord)

    @scoped.get("/agents/{agent_id}/revisions/{revision_id}", response_model=RevisionRecord)
    async def revision(project_id: str, agent_id: str, revision_id: str):
        async with db.sessions() as session:
            row = await selected_revision(session, revision_id)
            if row.agent_id != agent_id:
                raise HTTPException(404, "Agent revision not found")
            return record(row, RevisionRecord)

    @scoped.get("/connection-bindings", response_model=list[BindingRecord])
    async def connection_bindings(project_id: str):
        records = []
        for binding, entry in bindings_config().items():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("projects"), list)
                or project_id not in entry["projects"]
            ):
                continue
            try:
                connection = AgentConnection(
                    binding=binding,
                    endpoint=entry.get("endpoint"),
                    auth=entry.get("auth"),
                    deployment="metadata",
                    api_version="metadata",
                )
                approved_binding(connection, project_id, {binding: entry})
            except (ValueError, HTTPException):
                continue
            try:
                resolve_binding(connection, project_id, {binding: entry})
                configured = True
            except ValueError:
                configured = False
            records.append(
                {
                    "id": binding,
                    "endpoint": connection.endpoint,
                    "auth": connection.auth,
                    "configured": configured,
                }
            )
        return sorted(records, key=lambda row: row["id"])

    @scoped.post(
        "/chat-sessions", dependencies=execute, status_code=201, response_model=responses.ChatDetailV2
    )
    async def create_chat(project_id: str, body: CreateChatV2):
        async with db.write() as session:
            revision = await selected_revision(session, body.agent_revision_id, active=True)
            spec = AgentSpec.model_validate(revision.spec)
            validate_capabilities(spec, [], "mock")
            if not spec.supports_multi_turn or not spec.trace_available:
                raise HTTPException(409, "Playground requires multi-turn and tool trace capabilities")
            row = ChatSession(
                project_id=project_id,
                title=clean(body.title),
                agent_revision=revision.id,
                agent_revision_id=revision.id,
                legacy_workflow=False,
                pinned_revision=revision,
            )
            session.add(row)
            await session.flush()
            return chat_record(row, True)

    @scoped.post(
        "/evaluation-runs", dependencies=execute, status_code=202, response_model=responses.RunRecordV2
    )
    async def create_run(project_id: str, body: CreateRunV2):
        if clean(body.idempotency_key) != body.idempotency_key:
            raise HTTPException(422, "Idempotency keys must not contain sensitive data")
        async with db.write() as session:
            release = await get_or_404(session, Release, body.release_id)
            revision = await selected_revision(session, body.agent_revision_id)
            existing = await session.scalar(select(Run).where(Run.idempotency_key == body.idempotency_key))
            if existing:
                if (
                    existing.legacy_workflow
                    or existing.release_id != body.release_id
                    or existing.agent_revision_id != revision.id
                    or existing.mode != body.mode
                    or existing.lineage["judge"].get("selection", "none") != body.judge
                ):
                    raise HTTPException(409, "Idempotency key reused with different parameters")
                return run_record(existing, release.name)
            await require_active_project(session, project_id)
            if revision.agent.archived:
                raise HTTPException(409, "Agent is archived")
            cases = await release_cases(session, release.id)
            spec = AgentSpec.model_validate(revision.spec)
            validate_capabilities(spec, cases, body.mode)
            execution_key(spec, project_id, body.mode, settings)
            judging = judge_lineage(settings, cases, body.judge)
            pending = await pending_count(session, Run)
            if pending >= settings.max_pending:
                raise HTTPException(429, "Evaluation queue capacity exceeded")
            lineage = {
                "sdk_version": SDK_VERSION,
                "schema_version": "2",
                "project_id": project_id,
                "release_id": release.id,
                "content_hash": release.content_hash,
                "agent_id": revision.agent_id,
                "agent_revision": revision.id,
                "agent_spec": spec.model_dump(mode="json"),
                "agent_spec_hash": revision.spec_hash,
                "agent_package_version": "0.2.0",
                "mode": body.mode,
                "fixture_version": spec.fixture_version,
                "judge": judging,
                "synthetic": True,
                "executor": LOCAL_IDENTITY,
            }
            row = Run(
                project_id=project_id,
                release_id=release.id,
                agent_revision=revision.id,
                agent_revision_id=revision.id,
                legacy_workflow=False,
                pinned_revision=revision,
                mode=body.mode,
                idempotency_key=body.idempotency_key,
                lineage=lineage,
            )
            session.add(row)
            await session.flush()
            add_event(session, "run:" + row.id, "status", {"status": "queued", "gate": None})
            return run_record(row, release.name)

    @scoped.get(
        "/dataset-releases/{release_id}/export",
        response_model=None,
        response_class=Response,
        responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    async def export_release(
        project_id: str,
        release_id: str,
        agent_revision_id: str,
        mode: Literal["mock", "live"],
        judge: Literal["none", "azure"] = "none",
    ):
        async with db.sessions() as session:
            release = await get_or_404(session, Release, release_id)
            revision = await selected_revision(session, agent_revision_id)
            cases = await release_cases(session, release_id)
            validate_capabilities(AgentSpec.model_validate(revision.spec), cases, mode)
            # Export is a history read: agent bindings and credentials need not remain installed.
            judging = judge_lineage(settings, cases, judge, execution=False)
            try:
                data = await asyncio.to_thread(export_bundle_v2, release, cases, revision, mode, judging)
            except ValueError:
                raise HTTPException(
                    409, "Release content or redaction policy changed; publish a reviewed release"
                ) from None
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="goldenloop-release.zip"',
                "Cache-Control": "no-store",
            },
        )

    overrides = {
        responses.CaseRecord: responses.CaseRecordV2,
        list[responses.CaseRecord]: list[responses.CaseRecordV2],
        responses.ImportCommit: responses.ImportCommitV2,
        responses.ChatRecord: responses.ChatRecordV2,
        list[responses.ChatRecord]: list[responses.ChatRecordV2],
        responses.ChatDetail: responses.ChatDetailV2,
        responses.RunRecord: responses.RunRecordV2,
        list[responses.RunRecord]: list[responses.RunRecordV2],
        responses.RunDetail: responses.RunDetailV2,
    }
    replaced = {
        ("POST", "/chat-sessions"),
        ("POST", "/evaluation-runs"),
        ("GET", "/dataset-releases/{release_id}/export"),
    }
    for route in legacy_router.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path.removeprefix("/api/v1")
        if path == "/health" or any((method, path) in replaced for method in route.methods):
            continue
        scoped.add_api_route(
            path,
            route.endpoint,
            methods=route.methods,
            status_code=route.status_code,
            response_model=overrides.get(route.response_model, route.response_model),
            response_class=route.response_class,
            responses=route.responses,
            dependencies=[d for d in route.dependencies if d.dependency is not request_scope],
            name="v2_" + route.name,
        )
    app.include_router(registry)
    app.include_router(scoped)
