import asyncio
import importlib.util
import json
import os
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from goldenloop_eval import SDK_VERSION, Case, Turn, release_hash
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import responses
from .approval import validate_expectations
from .bootstrap import SCHEMA_REVISION
from .config import SanitizationError, Settings, clean
from .db import (
    CaseHead,
    CaseRevision,
    ChatSession,
    Database,
    Event,
    Feedback,
    Import,
    MessageCommand,
    Release,
    ReleaseCase,
    Run,
    add_event,
    pending_count,
    require_active_project,
    uid,
)
from .exports import MAX_RELEASE_BYTES, cases_bytes, export_bundle
from .imports import ImportError, mapped_cases, parse_upload, preview
from .judging import judge_lineage
from .runner import TERMINAL, Runner
from .schemas import (
    Approve,
    CommitImport,
    CreateCase,
    CreateChat,
    CreateFeedback,
    CreateRelease,
    CreateRun,
    EditCase,
    ReviewFeedback,
    SendMessage,
)
from .scope import current_scope, execution_metadata, request_scope
from .security import LOCAL_IDENTITY, LocalOnlyMiddleware, process_lock, require


def case_record(row):
    result = {"case": row.payload, "status": row.status, "reviewer": row.reviewer, "reason": row.reason}
    scope = current_scope.get()
    if scope and not scope.legacy:
        result["project_id"] = scope.project_id
    return result


def release_record(row):
    return {key: getattr(row, key) for key in ("id", "name", "created_at", "content_hash", "case_count")}


def chat_record(row, detail=False):
    keys = ["id", "title", "agent_revision", "created_at", "status", "trace_complete", "error"]
    if detail:
        keys += ["messages", "tool_calls"]
    result = {**{key: getattr(row, key) for key in keys}, "mode": "mock"}
    if current_scope.get() and not current_scope.get().legacy:
        result.update(execution_metadata(row))
    return result


def feedback_record(row):
    return {
        **row.payload,
        "id": row.id,
        "session_id": row.session_id,
        "created_at": row.created_at,
        "candidate_id": row.candidate_id,
    }


def run_record(row, release_name=None, detail=False):
    keys = ["id", "release_id", "agent_revision", "mode", "status", "gate", "created_at"]
    if detail:
        keys += ["results", "error", "lineage"]
    result = {**{key: getattr(row, key) for key in keys}, "release_name": release_name}
    if current_scope.get() and not current_scope.get().legacy:
        result.update(execution_metadata(row))
    return result


async def get_or_404(session, model, key):
    value = await session.get(model, key)
    if value is None:
        raise HTTPException(404, "Record not found")
    scope = current_scope.get()
    if scope and scope.writing and isinstance(value, ChatSession):
        revision = value.pinned_revision
        if revision and revision.agent.archived:
            raise HTTPException(409, "Agent is archived")
    if scope and scope.writing and isinstance(value, Feedback):
        await get_or_404(session, ChatSession, value.session_id)
    return value


async def latest_case(session, case_id):
    head = await get_or_404(session, CaseHead, case_id)
    return await get_or_404(session, CaseRevision, (case_id, head.latest))


def validate_case(case):
    payload = clean(case.model_dump(mode="json"))
    if len(json.dumps(payload)) > 128 * 1024 or len(case.turns) > 50 or len(case.checks) > 100:
        raise HTTPException(422, "Case exceeds size, turn, or check limits")
    if len(case.id) > 200 or "/" in case.id or "\\" in case.id or clean(case.id) != case.id:
        raise HTTPException(422, "Case ID must be a path-safe identifier of at most 200 characters")
    if case.fixture_version != "synthetic-v1":
        raise HTTPException(422, "Only synthetic-v1 fixtures are supported")
    if any(check.turn is not None and check.turn >= len(case.turns) for check in case.checks):
        raise HTTPException(422, "Check turn is outside the case")
    payload["source"] = {**payload["source"], "synthetic": True}
    return Case.model_validate(payload)


async def insert_case(session, case, reason):
    case = validate_case(case)
    if case.revision != 1:
        raise HTTPException(422, "New cases must begin at revision 1")
    if await session.get(CaseHead, case.id):
        raise HTTPException(409, "Case ID already exists")
    session.add(CaseHead(id=case.id, latest=1))
    await session.flush()
    row = CaseRevision(
        case_id=case.id,
        revision=1,
        payload=case.model_dump(mode="json"),
        status="candidate",
        reason=clean(reason),
    )
    session.add(row)
    await session.flush()
    return row


def live_ready(settings):
    keys = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_CHAT_COMPLETION_MODEL", "AZURE_OPENAI_API_VERSION")
    endpoint = urlsplit(os.getenv(keys[0], ""))
    if (
        not settings.allow_live
        or any(not os.getenv(key) for key in keys)
        or endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or importlib.util.find_spec("agent_framework") is None
    ):
        raise HTTPException(
            409,
            "Live mode requires explicit synthetic-data opt-in, live dependencies, and Azure configuration",
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings)
    runner = Runner(db, settings)

    @asynccontextmanager
    async def lifespan(app):
        if not settings.local_demo:
            yield
            await db.engine.dispose()
            return
        if not settings.db_path.is_file():
            raise RuntimeError("Database is not bootstrapped. Run python -m goldenloop_api.bootstrap --seed")
        with process_lock(settings.data_dir / "api.lock"):
            try:
                async with db.sessions() as session:
                    try:
                        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
                    except SQLAlchemyError:
                        raise RuntimeError(
                            "Database is not bootstrapped. Run python -m goldenloop_api.bootstrap"
                        ) from None
                    if revision != SCHEMA_REVISION:
                        raise RuntimeError("Database schema mismatch. Run python -m goldenloop_api.bootstrap")
                await runner.reconcile()
                task = asyncio.create_task(runner.loop())
                try:
                    yield
                finally:
                    # Let queue polling finish its short DB transaction before cancelling model work.
                    # Cancelling aiosqlite connection creation can strand its worker beyond loop shutdown.
                    runner.stopping = True
                    runner.wake.set()
                    with suppress(asyncio.CancelledError):
                        await task
            finally:
                await db.engine.dispose()

    app = FastAPI(
        title="GoldenLoop Local Synthetic API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
        description="Loopback-only synthetic workbench. Fixed local identity, NOT shared authentication. "
        "Explicit bootstrap required. SSE contains completed events, not token streaming.",
    )
    app.state.settings, app.state.db, app.state.runner = settings, db, runner
    app.add_middleware(LocalOnlyMiddleware, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        # Pydantic's input/context fields may contain raw secrets or exception objects.
        return JSONResponse(
            {
                "detail": [
                    {"loc": list(error["loc"]), "type": error["type"], "msg": "Invalid value"}
                    for error in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.exception_handler(ValidationError)
    async def invalid_model(_request, _exc):
        return JSONResponse({"detail": "Invalid canonical data"}, status_code=422)

    @app.exception_handler(SanitizationError)
    async def unsafe_data(_request, _exc):
        return JSONResponse({"detail": "Input cannot be safely sanitized"}, status_code=422)

    @app.exception_handler(ImportError)
    async def invalid_import(_request, exc):
        return JSONResponse({"detail": clean(str(exc)), "errors": [clean(str(exc))]}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def integrity_error(_request, _exc):
        return JSONResponse(
            {"detail": "Concurrent change or duplicate record; reload and retry"}, status_code=409
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, _exc):
        return JSONResponse({"detail": "Database temporarily unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _exc):
        return JSONResponse({"detail": "Internal operation failed"}, status_code=500)

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require("read")), Depends(request_scope)])
    review = [Depends(require("review"))]
    execute = [Depends(require("execute"))]

    @api.get("/health", response_model=responses.Health)
    async def health():
        return {"status": "ok", "mode": "local-synthetic", "sdk_version": SDK_VERSION}

    @api.get("/summary", response_model=responses.Summary)
    async def summary():
        async with db.sessions() as session:
            candidates = await session.scalar(
                select(func.count())
                .select_from(CaseRevision)
                .join(
                    CaseHead,
                    (CaseHead.id == CaseRevision.case_id) & (CaseHead.latest == CaseRevision.revision),
                )
                .where(CaseRevision.status == "candidate")
            )
            counts = {
                key: await session.scalar(select(func.count()).select_from(model))
                for key, model in (("releases", Release), ("runs", Run), ("feedback", Feedback))
            }
            return {"candidates": candidates, **counts}

    @api.get("/cases", response_model=list[responses.CaseRecord])
    async def cases():
        async with db.sessions() as session:
            rows = await session.scalars(
                select(CaseRevision)
                .join(
                    CaseHead,
                    (CaseHead.id == CaseRevision.case_id) & (CaseHead.latest == CaseRevision.revision),
                )
                .order_by(CaseRevision.created_at, CaseRevision.case_id)
            )
            return [case_record(row) for row in rows]

    @api.post("/cases", dependencies=review, status_code=201, response_model=responses.CaseRecord)
    async def create_case(body: CreateCase):
        async with db.write() as session:
            return case_record(await insert_case(session, body.case, body.reason))

    @api.get("/cases/{case_id}", response_model=responses.CaseRecord)
    async def get_case(case_id: str):
        async with db.sessions() as session:
            return case_record(await latest_case(session, case_id))

    @api.put("/cases/{case_id}", dependencies=review, response_model=responses.CaseRecord)
    async def edit_case(case_id: str, body: EditCase):
        async with db.write() as session:
            head = await get_or_404(session, CaseHead, case_id)
            if head.latest != body.expected_revision:
                raise HTTPException(409, "Stale case revision")
            if "id" in body.case.model_fields_set and body.case.id != case_id:
                raise HTTPException(422, "Body case ID must match path")
            case = validate_case(body.case.model_copy(update={"id": case_id, "revision": head.latest + 1}))
            head.latest = case.revision
            row = CaseRevision(
                case_id=case_id,
                revision=case.revision,
                payload=case.model_dump(mode="json"),
                status="candidate",
                reason=clean(body.reason),
            )
            session.add(row)
            await session.flush()
            return case_record(row)

    @api.post("/cases/{case_id}/approve", dependencies=review, response_model=responses.CaseRecord)
    async def approve_case(case_id: str, body: Approve):
        async with db.write() as session:
            row = await latest_case(session, case_id)
            if row.revision != body.revision:
                raise HTTPException(409, "Only the current revision can be approved")
            if not body.reason.strip() or not any(check["required"] for check in row.payload["checks"]):
                raise HTTPException(
                    422, "Approval requires a reason and at least one required actionable check"
                )
            await asyncio.to_thread(validate_expectations, Case.model_validate(row.payload))
            if row.status == "approved":
                return case_record(row)
            row.status, row.reviewer, row.reason = "approved", LOCAL_IDENTITY, clean(body.reason)
            return case_record(row)

    @api.post("/imports/preview", dependencies=review, response_model=responses.ImportPreview)
    async def import_preview(
        file: Annotated[UploadFile, File()], sheet: Annotated[str | None, Form()] = None
    ):
        try:
            raw = await file.read(settings.max_upload_bytes + 1)
            if len(raw) > settings.max_upload_bytes:
                raise HTTPException(413, "Upload exceeds byte limit")
            payload = await asyncio.to_thread(parse_upload, raw, file.filename or "", sheet, settings)
        finally:
            await file.close()
        async with db.write() as session:
            row = Import(payload=payload)
            session.add(row)
            await session.flush()
            return {"id": row.id, **preview(payload)}

    @api.post("/imports/{import_id}/commit", dependencies=review, response_model=responses.ImportCommit)
    async def import_commit(import_id: str, body: CommitImport):
        async with db.write() as session:
            row = await get_or_404(session, Import, import_id)
            if row.committed:
                raise HTTPException(409, "Import already committed")
            imported = mapped_cases(row.payload, body.mapping, body.sheet, import_id)
            if len(imported) > settings.max_cases:
                raise ImportError("Case limit exceeded")
            seen = set()
            fingerprints = set()
            if body.duplicate_policy == "reject":
                existing = (
                    await session.scalars(
                        select(CaseRevision).join(
                            CaseHead,
                            (CaseHead.id == CaseRevision.case_id)
                            & (CaseHead.latest == CaseRevision.revision),
                        )
                    )
                ).all()
                fingerprints = {
                    json.dumps(
                        {"context": item.payload["context"], "turns": item.payload["turns"]}, sort_keys=True
                    )
                    for item in existing
                }
            records = []
            for case in imported:
                fingerprint = json.dumps(
                    {"context": case.context, "turns": [t.model_dump() for t in case.turns]}, sort_keys=True
                )
                if body.duplicate_policy == "reject":
                    if case.id in seen or await session.get(CaseHead, case.id) or fingerprint in fingerprints:
                        raise HTTPException(409, "Duplicate case ID or scenario; entire import rejected")
                    fingerprints.add(fingerprint)
                else:
                    original_id = case.id
                    case = case.model_copy(
                        update={"id": uid(), "source": {**case.source, "uploaded_case_id": original_id}}
                    )
                seen.add(case.id)
                records.append(
                    case_record(
                        await insert_case(session, case, "Imported synthetic candidate; not reviewed")
                    )
                )
            row.committed = True
            return {"cases": records, "errors": []}

    @api.get("/dataset-releases", response_model=list[responses.ReleaseRecord])
    async def releases():
        async with db.sessions() as session:
            return [
                release_record(row)
                for row in await session.scalars(select(Release).order_by(Release.created_at))
            ]

    @api.post(
        "/dataset-releases", dependencies=review, status_code=201, response_model=responses.ReleaseRecord
    )
    async def create_release(body: CreateRelease):
        if len(set(body.case_ids)) != len(body.case_ids) or len(body.case_ids) > settings.max_cases:
            raise HTTPException(422, "Release case IDs must be unique and within limits")
        async with db.write() as session:
            rows = [await latest_case(session, case_id) for case_id in body.case_ids]
            if any(row.revision != body.expected_revisions[row.case_id] for row in rows):
                raise HTTPException(409, "Selected case revision changed; reload and select revisions again")
            if any(row.status != "approved" for row in rows):
                raise HTTPException(409, "Every selected latest revision must be approved")
            selected = [Case.model_validate(clean(row.payload)) for row in rows]
            for case in selected:
                await asyncio.to_thread(validate_expectations, case)
            if len(cases_bytes(selected)) > MAX_RELEASE_BYTES:
                raise HTTPException(422, "Release exceeds the portable bundle size limit")
            if any(case.model_dump(mode="json") != row.payload for case, row in zip(selected, rows)):
                raise HTTPException(409, "Redaction policy changed; create and review a new case revision")
            release = Release(
                name=clean(body.name), content_hash=release_hash(selected), case_count=len(selected)
            )
            session.add(release)
            await session.flush()
            for case in selected:
                session.add(
                    ReleaseCase(
                        release_id=release.id,
                        case_id=case.id,
                        revision=case.revision,
                        payload=case.model_dump(mode="json"),
                    )
                )
            return release_record(release)

    @api.get("/dataset-releases/{release_id}", response_model=responses.ReleaseDetail)
    async def get_release(release_id: str):
        async with db.sessions() as session:
            release = await get_or_404(session, Release, release_id)
            cases = await session.scalars(
                select(ReleaseCase).where(ReleaseCase.release_id == release_id).order_by(ReleaseCase.case_id)
            )
            return {**release_record(release), "cases": [row.payload for row in cases]}

    @api.get(
        "/dataset-releases/{release_id}/export",
        response_model=None,
        response_class=Response,
        responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    async def export_release(release_id: str):
        async with db.sessions() as session:
            release = await get_or_404(session, Release, release_id)
            payloads = [
                row.payload
                for row in await session.scalars(
                    select(ReleaseCase)
                    .where(ReleaseCase.release_id == release_id)
                    .order_by(ReleaseCase.case_id)
                )
            ]
        try:
            data = await asyncio.to_thread(export_bundle, release_id, payloads, release.content_hash)
        except ValueError:
            raise HTTPException(
                409, "Release content hash or redaction policy changed; publish a newly reviewed release"
            ) from None
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="goldenloop-release.zip"',
                "Cache-Control": "no-store",
            },
        )

    @api.get("/chat-sessions", response_model=list[responses.ChatRecord])
    async def chats():
        async with db.sessions() as session:
            return [
                chat_record(row)
                for row in await session.scalars(select(ChatSession).order_by(ChatSession.created_at))
            ]

    @api.post("/chat-sessions", dependencies=execute, status_code=201, response_model=responses.ChatDetail)
    async def create_chat(body: CreateChat):
        async with db.write() as session:
            from .registry import selected_revision

            revision = await selected_revision(session, "synthetic-" + body.agent_revision, active=True)
            chat = ChatSession(
                title=clean(body.title),
                agent_revision=body.agent_revision,
                agent_revision_id=revision.id,
                pinned_revision=revision,
            )
            session.add(chat)
            await session.flush()
            return chat_record(chat, True)

    @api.get("/chat-sessions/{session_id}", response_model=responses.ChatDetail)
    async def get_chat(session_id: str):
        async with db.sessions() as session:
            return chat_record(await get_or_404(session, ChatSession, session_id), True)

    @api.post(
        "/chat-sessions/{session_id}/messages",
        dependencies=execute,
        status_code=202,
        response_model=responses.MessageAccepted,
    )
    async def send_message(session_id: str, body: SendMessage):
        if not body.content.strip():
            raise HTTPException(422, "Message must not be blank")
        async with db.write() as session:
            chat = await get_or_404(session, ChatSession, session_id)
            if chat.status in {"queued", "running"}:
                raise HTTPException(409, "A message is already pending for this session")
            if chat.status in {"failed", "interrupted"}:
                raise HTTPException(409, "Start a new session after an incomplete turn")
            turn = len([message for message in chat.messages if message["role"] == "user"])
            if turn >= 50:
                raise HTTPException(422, "Session turn limit exceeded")
            pending = await pending_count(session, MessageCommand)
            if pending >= settings.max_pending:
                raise HTTPException(429, "Chat queue capacity exceeded")
            command = MessageCommand(session_id=session_id, turn=turn, content=clean(body.content))
            session.add(command)
            await session.flush()
            chat.status, chat.trace_complete = "queued", False
            add_event(session, "chat:" + session_id, "status", {"status": "queued", "id": command.id})
            return {"id": command.id, "session_id": session_id}

    @api.post("/feedback", dependencies=review, status_code=201, response_model=responses.FeedbackRecord)
    async def create_feedback(body: CreateFeedback):
        async with db.write() as session:
            chat = await get_or_404(session, ChatSession, body.session_id)
            if not any(
                message.get("turn") == body.turn and message["role"] == "assistant"
                for message in chat.messages
            ):
                raise HTTPException(422, "Feedback requires an observed completed turn")
            if body.target == "tool":
                if not body.tool_call_id or not any(
                    call["id"] == body.tool_call_id and call["turn"] == body.turn for call in chat.tool_calls
                ):
                    raise HTTPException(422, "Tool call must belong to the selected session and turn")
            elif body.tool_call_id is not None:
                raise HTTPException(
                    422, "Answer and missing-tool feedback cannot reference an observed tool call"
                )
            payload = clean(
                {
                    **body.model_dump(mode="json"),
                    "reviewer": LOCAL_IDENTITY,
                    "status": "unresolved",
                    "reason": None,
                }
            )
            if len(json.dumps(payload)) > 32000:
                raise HTTPException(422, "Feedback exceeds size limit")
            row = Feedback(session_id=body.session_id, payload=payload)
            session.add(row)
            await session.flush()
            return feedback_record(row)

    @api.get("/feedback", response_model=list[responses.FeedbackRecord])
    async def feedback_list():
        async with db.sessions() as session:
            return [
                feedback_record(row)
                for row in await session.scalars(select(Feedback).order_by(Feedback.created_at))
            ]

    @api.post("/feedback/{feedback_id}/review", dependencies=review, response_model=responses.FeedbackRecord)
    async def review_feedback(feedback_id: str, body: ReviewFeedback):
        async with db.write() as session:
            feedback = await get_or_404(session, Feedback, feedback_id)
            feedback.payload = clean({**feedback.payload, **body.model_dump(), "reviewer": LOCAL_IDENTITY})
            return feedback_record(feedback)

    @api.post("/feedback/{feedback_id}/candidate", dependencies=review, response_model=responses.CaseRecord)
    async def feedback_candidate(feedback_id: str):
        async with db.write() as session:
            feedback = await get_or_404(session, Feedback, feedback_id)
            if feedback.candidate_id:
                return case_record(await latest_case(session, feedback.candidate_id))
            chat = await get_or_404(session, ChatSession, feedback.session_id)
            turns = [
                Turn(user=message["content"])
                for message in chat.messages
                if message["role"] == "user" and message["turn"] <= feedback.payload["turn"]
            ]
            case = Case(
                title="Feedback: " + chat.title,
                tags=["synthetic", "feedback"],
                turns=turns,
                source={
                    "type": "feedback",
                    "feedback_id": feedback_id,
                    "session_id": chat.id,
                    "turn": feedback.payload["turn"],
                    "synthetic": True,
                },
            )
            if current_scope.get() and not current_scope.get().legacy:
                case.source.update(execution_metadata(chat))
            row = await insert_case(
                session, case, "Observed user history only; expectations require independent review"
            )
            feedback.candidate_id = case.id
            return case_record(row)

    @api.get("/evaluation-runs", response_model=list[responses.RunRecord])
    async def runs():
        async with db.sessions() as session:
            return [
                run_record(run, name)
                for run, name in (
                    await session.execute(select(Run, Release.name).join(Release).order_by(Run.created_at))
                ).all()
            ]

    @api.post("/evaluation-runs", dependencies=execute, status_code=202, response_model=responses.RunRecord)
    async def create_run(body: CreateRun):
        if clean(body.idempotency_key) != body.idempotency_key:
            raise HTTPException(422, "Idempotency keys must not contain credentials or personal data")
        async with db.write() as session:
            existing = await session.scalar(select(Run).where(Run.idempotency_key == body.idempotency_key))
            if existing:
                if (existing.release_id, existing.agent_revision, existing.mode) != (
                    body.release_id,
                    body.agent_revision,
                    body.mode,
                ) or existing.lineage["judge"].get("selection", "none") != body.judge:
                    raise HTTPException(409, "Idempotency key reused with different parameters")
                release = await get_or_404(session, Release, existing.release_id)
                return run_record(existing, release.name)
            await require_active_project(session, current_scope.get().project_id)
            release = await get_or_404(session, Release, body.release_id)
            from .registry import selected_revision

            revision = await selected_revision(session, "synthetic-" + body.agent_revision, active=True)
            if body.mode == "live":
                live_ready(settings)
            selected = [
                Case.model_validate(row.payload)
                for row in await session.scalars(
                    select(ReleaseCase)
                    .where(ReleaseCase.release_id == release.id)
                    .order_by(ReleaseCase.case_id)
                )
            ]
            judging = judge_lineage(settings, selected, body.judge)
            pending = await pending_count(session, Run)
            if pending >= settings.max_pending:
                raise HTTPException(429, "Evaluation queue capacity exceeded")
            lineage = clean(
                {
                    "sdk_version": SDK_VERSION,
                    "schema_version": "1",
                    "release_id": release.id,
                    "content_hash": release.content_hash,
                    "agent_package_version": "0.2.0",
                    "agent_revision": body.agent_revision,
                    "mode": body.mode,
                    "fixture_version": "synthetic-v1",
                    "judge": judging,
                    "agent_model": os.getenv("AZURE_OPENAI_CHAT_COMPLETION_MODEL")
                    if body.mode == "live"
                    else None,
                    "agent_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT") if body.mode == "live" else None,
                    "agent_api_version": os.getenv("AZURE_OPENAI_API_VERSION")
                    if body.mode == "live"
                    else None,
                    "synthetic": True,
                    "executor": LOCAL_IDENTITY,
                }
            )
            run = Run(
                **body.model_dump(exclude={"judge"}),
                lineage=lineage,
                agent_revision_id=revision.id,
                pinned_revision=revision,
            )
            session.add(run)
            await session.flush()
            add_event(session, "run:" + run.id, "status", {"status": "queued", "gate": None})
            return run_record(run, release.name)

    @api.get("/evaluation-runs/{run_id}", response_model=responses.RunDetail)
    async def get_run(run_id: str):
        async with db.sessions() as session:
            run = await get_or_404(session, Run, run_id)
            release = await get_or_404(session, Release, run.release_id)
            return run_record(run, release.name, True)

    @api.post("/evaluation-runs/{run_id}/cancel", dependencies=execute, response_model=responses.RunDetail)
    async def cancel_run(run_id: str):
        async with db.write() as session:
            run = await get_or_404(session, Run, run_id)
            if run.status not in TERMINAL:
                run.status, run.gate, run.error = "cancelled", "error", "Cancellation requested"
                add_event(session, "run:" + run_id, "status", {"status": "cancelled", "gate": "error"})
            release = await get_or_404(session, Release, run.release_id)
            result = run_record(run, release.name, True)
        task = runner.tasks.get("run:" + run_id)
        if task and not task.done() and not task.cancelling():
            task.cancel()
        return result

    async def events(request, record_id, model, prefix):
        async def check_record(session):
            await get_or_404(session, model, record_id)

        await db.read(check_record)
        try:
            cursor = int(request.headers.get("last-event-id", request.query_params.get("after", "0")))
            if not 0 <= cursor <= 2**63 - 1:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "Event cursor must be a nonnegative signed 64-bit integer") from None

        async def stream():
            nonlocal cursor

            async def read_batch(session):
                # Read status first: a completion between queries must not hide its final events.
                record = await session.get(model, record_id)
                terminal = record.status in TERMINAL or record.status == "idle"
                rows = (
                    await session.scalars(
                        select(Event)
                        .where(Event.stream == prefix + record_id, Event.id > cursor)
                        .order_by(Event.id)
                        .limit(100)
                    )
                ).all()
                return terminal, rows

            while True:
                terminal, rows = await db.read(read_batch)
                for row in rows:
                    cursor = row.id
                    yield f"id: {row.id}\nevent: {row.kind}\ndata: {json.dumps(clean(row.payload), ensure_ascii=True)}\n\n"
                if terminal and len(rows) < 100:
                    return
                if await request.is_disconnected():
                    return
                if not rows:
                    yield ": waiting for completed events\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
        )

    @api.get(
        "/chat-sessions/{session_id}/events",
        response_model=None,
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    )
    async def chat_events(session_id: str, request: Request):
        return await events(request, session_id, ChatSession, "chat:")

    @api.get(
        "/evaluation-runs/{run_id}/events",
        response_model=None,
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    )
    async def run_events(run_id: str, request: Request):
        return await events(request, run_id, Run, "run:")

    app.include_router(api)
    from .registry import install_v2

    install_v2(app, api, db, settings)
    return app


app = create_app()
