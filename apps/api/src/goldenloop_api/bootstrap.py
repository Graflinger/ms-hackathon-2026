import argparse
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from goldenloop_eval import Case, Check, Turn
from sqlalchemy import create_engine, event

from .config import Settings, clean
from .db import CaseHead, CaseRevision, Database, configure_sqlite, journal_mode

SCHEMA_REVISION = "0002"


def migrate(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + settings.db_path.as_posix())
    event.listen(engine, "connect", configure_sqlite)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f"PRAGMA journal_mode={journal_mode()}")
            connection.commit()
            # Serialize concurrent bootstrap attempts before Alembic checks its version table.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
    finally:
        engine.dispose()


async def seed(settings: Settings):
    db = Database(settings)
    try:
        async with db.write() as session:
            if await session.get(CaseHead, "synthetic-customer-lookup"):
                return
            case = Case(
                id="synthetic-customer-lookup",
                title="Synthetic customer lookup",
                tags=["synthetic"],
                source={"type": "synthetic-seed"},
                turns=[Turn(user="Look up synthetic customer C-123")],
                checks=[
                    Check(kind="content_contains", config={"value": "C-123"}),
                    Check(
                        kind="tool_arguments",
                        config={
                            "tool": "lookup_customer",
                            "path": "customer_id",
                            "operator": "equals",
                            "value": "C-123",
                        },
                    ),
                ],
            )
            session.add(CaseHead(id=case.id, latest=1))
            await session.flush()
            session.add(
                CaseRevision(
                    case_id=case.id,
                    revision=1,
                    payload=clean(case.model_dump(mode="json")),
                    reason="Synthetic seed; review before publication",
                )
            )
    finally:
        await db.engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Idempotently migrate the local GoldenLoop database. Never resets existing data.",
        epilog="Set GOLDENLOOP_LOCAL_DEMO=true, optionally GOLDENLOOP_DATA_DIR (default .goldenloop). "
        "Then run: uvicorn goldenloop_api.main:app --host 127.0.0.1 --port 8000 --workers 1. "
        "Run from apps/api so the default data directory is ignored. Local synthetic data only; "
        "this fixed identity is NOT shared-deployment authentication. Live sandbox calls require "
        "GOLDENLOOP_ALLOW_LIVE_SYNTHETIC=true plus goldenloop-demo-agent[live] and "
        "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_CHAT_COMPLETION_MODEL, AZURE_OPENAI_API_VERSION "
        "and API key or Azure CLI credentials. Redaction is best-effort, not a DLP guarantee. "
        "Do not supply real customer data. Chat is deterministic mock-only. Azure judging requires "
        "GOLDENLOOP_ALLOW_LIVE_SYNTHETIC_JUDGE=true, goldenloop-eval[live], "
        "GOLDENLOOP_JUDGE_ENDPOINT, GOLDENLOOP_JUDGE_DEPLOYMENT, GOLDENLOOP_JUDGE_API_VERSION, "
        "GOLDENLOOP_JUDGE_API_KEY and explicit judge=azure in the run request. Agent mode and judge "
        "selection are independent; default mock/none never makes cloud calls. At most 20 judge checks per run.",
    )
    parser.add_argument("--seed", action="store_true", help="Add an unapproved synthetic example if absent")
    args = parser.parse_args(argv)
    settings = Settings()
    if not settings.local_demo:
        parser.error(
            "Explicit GOLDENLOOP_LOCAL_DEMO=true is required; shared authentication is not implemented"
        )
    migrate(settings)
    if args.seed:
        asyncio.run(seed(settings))
    print(f"Database ready: {settings.db_path} (journal={journal_mode()})")


if __name__ == "__main__":
    main()
