import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from goldenloop_eval import AgentSpec, Case, Turn, agent_spec_hash, release_hash
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

from goldenloop_api.bootstrap import migrate
from goldenloop_api.db import AgentRevision, CaseHead, configure_sqlite


def old_database(settings, label="fixed"):
    engine = create_engine("sqlite:///" + settings.db_path.as_posix())
    event.listen(engine, "connect", configure_sqlite)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/goldenloop_api/migrations")
    )
    case = Case(id="old-case", title="Historical", turns=[Turn(user="C-123")])
    payload = json.dumps(case.model_dump(mode="json"), indent=3)
    content_hash = release_hash([case])
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        connection.execute(text("INSERT INTO cases VALUES ('old-case', 1)"))
        connection.execute(
            text("""INSERT INTO case_revisions VALUES
            ('old-case', 1, :payload, 'approved', 'reviewer', 'old reason', 'old time')"""),
            {"payload": payload},
        )
        connection.execute(
            text("INSERT INTO imports VALUES ('old-import', :payload, 1)"), {"payload": '{ "old": true }'}
        )
        connection.execute(
            text("""INSERT INTO dataset_releases VALUES
            ('old-release', 'Historical', 'old time', :hash, 1)"""),
            {"hash": content_hash},
        )
        connection.execute(
            text("INSERT INTO release_cases VALUES ('old-release', 'old-case', 1, :payload)"),
            {"payload": payload},
        )
        connection.execute(
            text("""INSERT INTO chat_sessions VALUES
            ('old-chat', 'Historical', :label, 'old time', 'completed', '[ ]', '[ ]', 1, NULL)"""),
            {"label": label},
        )
        connection.execute(
            text("""INSERT INTO message_commands VALUES
            ('old-command', 'old-chat', 0, 'C-123', 'completed', 'old time')""")
        )
        connection.execute(
            text("""INSERT INTO feedback VALUES
            ('old-feedback', 'old-chat', '{ "old": true }', 'old-case', 'old time')""")
        )
        for index, (status, mode) in enumerate(
            (("completed", "live"), ("queued", "mock"), ("running", "mock"))
        ):
            connection.execute(
                text("""INSERT INTO evaluation_runs VALUES
                (:id, 'old-release', :label, :mode, :key, :status, NULL, 'old time', :results, NULL, :lineage)"""),
                {
                    "id": f"old-run-{index}",
                    "label": label,
                    "mode": mode,
                    "key": f"key-{index}",
                    "status": status,
                    "results": '[ { "historical": true } ]',
                    "lineage": '{ "agent_revision": "fixed", "old": true }',
                },
            )
        connection.execute(
            text("INSERT INTO events VALUES (123, 'run:old-run-0', 'result', :payload)"),
            {"payload": '{ "old_evidence": [1, 2, 3] }'},
        )
        connection.commit()
    return engine


def snapshot(connection):
    tables = (
        "cases",
        "case_revisions",
        "imports",
        "dataset_releases",
        "release_cases",
        "chat_sessions",
        "message_commands",
        "feedback",
        "evaluation_runs",
        "events",
    )
    return {
        table: [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
        for table in tables
    }


def test_populated_upgrade_preserves_exact_evidence_and_constraints(settings):
    engine = old_database(settings)
    try:
        with engine.connect() as connection:
            before = snapshot(connection)
        migrate(settings)
        migrate(settings)
        with engine.connect() as connection:
            after = snapshot(connection)
            for table, rows in before.items():
                for old, new in zip(rows, after[table], strict=True):
                    assert {key: new[key] for key in old} == old
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert connection.exec_driver_sql("SELECT count(*) FROM projects").scalar() == 1
            assert connection.exec_driver_sql("SELECT count(*) FROM agent_revisions").scalar() == 2
            revisions = connection.exec_driver_sql(
                "SELECT id, spec, spec_hash, legacy FROM agent_revisions"
            ).all()
            for revision, payload, digest, legacy in revisions:
                spec = AgentSpec.model_validate_json(payload)
                assert spec.modes == ["mock"] and spec.connection is None and legacy
                assert digest == agent_spec_hash(spec) and revision == "synthetic-" + spec.variant
            assert after["evaluation_runs"][0]["agent_revision_id"] == "synthetic-fixed"
            for sql in (
                "UPDATE agent_revisions SET spec_hash='changed' WHERE id='synthetic-fixed'",
                "UPDATE cases SET project_id=NULL WHERE id='old-case'",
                "UPDATE chat_sessions SET agent_revision_id='synthetic-buggy' WHERE id='old-chat'",
                "INSERT INTO cases(id, latest) VALUES ('missing-owner', 1)",
            ):
                with pytest.raises(IntegrityError):
                    connection.exec_driver_sql(sql)
                connection.rollback()
    finally:
        engine.dispose()


def test_unknown_legacy_labels_roll_back_without_partial_schema(settings):
    engine = old_database(settings, "unknown-build")
    try:
        with engine.connect() as connection:
            before = snapshot(connection)
        with pytest.raises(RuntimeError, match="repair.*old-chat"):
            migrate(settings)
        with engine.connect() as connection:
            assert snapshot(connection) == before
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0001"
            assert (
                connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='projects'").first()
                is None
            )
            connection.exec_driver_sql("UPDATE chat_sessions SET agent_revision='fixed'")
            connection.exec_driver_sql("UPDATE evaluation_runs SET agent_revision='fixed'")
            connection.commit()
        migrate(settings)
    finally:
        engine.dispose()


def test_failure_after_ddl_rolls_back_real_bootstrap_transaction(settings, monkeypatch):
    engine = old_database(settings)
    try:
        with engine.connect() as connection:
            before = snapshot(connection)
        with monkeypatch.context() as patch:

            def fail_hash(_spec):
                raise RuntimeError("Injected failure after registry DDL")

            patch.setattr("goldenloop_eval.agent_spec_hash", fail_hash)
            with pytest.raises(RuntimeError, match="Injected failure"):
                migrate(settings)
        with engine.connect() as connection:
            assert snapshot(connection) == before
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0001"
            assert (
                connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='projects'").first()
                is None
            )
        migrate(settings)
    finally:
        engine.dispose()


async def test_direct_db_scope_constraints_and_default_ownership(app, client):
    from .test_projects import setup_project

    path, agent, revision = await setup_project(client)
    project_id = path.rsplit("/", 1)[-1]
    async with app.state.db.write() as session:
        row = CaseHead(id="defaults-demo", latest=1)
        session.add(row)
        await session.flush()
        assert row.project_id == "synthetic-demo"
    with pytest.raises(IntegrityError):
        async with app.state.db.write() as session:
            session.add(
                AgentRevision(
                    id="invalid-owner",
                    agent_id=agent["id"],
                    project_id="synthetic-demo",
                    number=2,
                    label="invalid",
                    spec=revision["spec"],
                    spec_hash=revision["spec_hash"],
                )
            )
            await session.flush()
    async with app.state.db.engine.connect() as connection:
        assert (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all() == []
        assert project_id != "synthetic-demo"
