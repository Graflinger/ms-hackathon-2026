import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from anyio import CancelScope
from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import Settings, clean


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


DEMO_PROJECT = "synthetic-demo"
DEMO_AGENT = "synthetic-customer-lookup"


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class AgentRevision(Base):
    __tablename__ = "agent_revisions"
    __table_args__ = (UniqueConstraint("agent_id", "number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String)
    spec: Mapped[dict] = mapped_column(JSON)
    spec_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)
    legacy: Mapped[bool] = mapped_column(default=False)
    agent: Mapped[Agent] = relationship(lazy="joined")

    @property
    def spec_provenance(self):
        return "mapping_only" if self.legacy else "registered"


class ProjectOwned:
    # Nullable in SQLite for additive migration; triggers enforce required ownership.
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id"), default=DEMO_PROJECT, nullable=True, index=True
    )


class ExecutionOwned(ProjectOwned):
    agent_revision_id: Mapped[str | None] = mapped_column(ForeignKey("agent_revisions.id"), nullable=True)
    legacy_workflow: Mapped[bool] = mapped_column(default=True, server_default="1")


class CaseHead(ProjectOwned, Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    latest: Mapped[int] = mapped_column(Integer)


class CaseRevision(Base):
    __tablename__ = "case_revisions"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="candidate")
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Import(ProjectOwned, Base):
    __tablename__ = "imports"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    payload: Mapped[dict] = mapped_column(JSON)
    committed: Mapped[bool] = mapped_column(default=False)


class Release(ProjectOwned, Base):
    __tablename__ = "dataset_releases"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)
    content_hash: Mapped[str] = mapped_column(String)
    case_count: Mapped[int] = mapped_column(Integer)


class ReleaseCase(Base):
    __tablename__ = "release_cases"
    __table_args__ = (
        ForeignKeyConstraint(["case_id", "revision"], ["case_revisions.case_id", "case_revisions.revision"]),
    )
    release_id: Mapped[str] = mapped_column(ForeignKey("dataset_releases.id"), primary_key=True)
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class ChatSession(ExecutionOwned, Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String)
    agent_revision: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)
    status: Mapped[str] = mapped_column(String, default="idle")
    messages: Mapped[list] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    trace_complete: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    pinned_revision: Mapped[AgentRevision | None] = relationship(lazy="joined")


class MessageCommand(Base):
    __tablename__ = "message_commands"
    __table_args__ = (UniqueConstraint("session_id", "turn"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Run(ExecutionOwned, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_run_project_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    release_id: Mapped[str] = mapped_column(ForeignKey("dataset_releases.id"), index=True)
    agent_revision: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    gate: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    results: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    lineage: Mapped[dict] = mapped_column(JSON)
    pinned_revision: Mapped[AgentRevision | None] = relationship(lazy="joined")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)


def add_event(session, stream: str, kind: str, payload: dict):
    session.add(Event(stream=stream, kind=kind, payload=clean(payload)))


def configure_sqlite(connection, _record=None):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def journal_mode() -> str:
    return "WAL" if sqlite3.sqlite_version_info >= (3, 51, 3) else "DELETE"


async def require_active_project(session, project_id):
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if project.archived:
        raise HTTPException(409, "Project is archived")


async def pending_count(session, model):
    # Core table columns deliberately bypass request ORM filters. Capacity is shared.
    table = model.__table__
    return await session.scalar(
        select(func.count()).select_from(table).where(table.c.status.in_(["queued", "running"]))
    )


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine("sqlite+aiosqlite:///" + settings.db_path.as_posix())
        event.listen(self.engine.sync_engine, "connect", configure_sqlite)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.lock = asyncio.Lock()

    async def read(self, operation):
        """Finish the entire short read and close its session before propagating cancellation.

        Cancelling aiosqlite between execute and cursor.close can strand a SQLite reader.
        Shielding only AsyncSession.close is too late. Never yield a stream from operation.
        """

        async def transaction():
            async with self.sessions() as session:
                return await operation(session)

        task = asyncio.create_task(transaction())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Starlette/AnyIO may repeatedly cancel the streaming task on disconnect.
            with CancelScope(shield=True):
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:  # noqa: BLE001 - retrieve failures but preserve caller cancellation
                        break
                if not task.cancelled():
                    task.exception()
            raise

    @asynccontextmanager
    async def write(self):
        # One writer per local process; model calls and SSE never hold this lock.
        async with self.lock, self.sessions.begin() as session:
            for attempt in range(3):
                try:
                    await session.execute(text("BEGIN IMMEDIATE"))
                    break
                except OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 2:
                        raise
                    await asyncio.sleep(0.05 * (attempt + 1))
            # Recheck after acquiring the writer reservation, not just at request entry.
            from .scope import current_scope

            scope = current_scope.get()
            if scope and scope.writing:
                await require_active_project(session, scope.project_id)
            yield session
