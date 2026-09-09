"""Add project ownership without rewriting historical canonical evidence."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from goldenloop_eval import AgentSpec, agent_spec_hash

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Validate before any DDL, including fresh bootstrap's enclosing transaction.
    unknown = []
    for table in ("chat_sessions", "evaluation_runs"):
        unknown += [
            (table, *row)
            for row in connection.execute(
                sa.text(
                    f"SELECT id, agent_revision FROM {table} WHERE agent_revision NOT IN ('buggy', 'fixed')"
                )
            )
        ]
    if unknown:
        raise RuntimeError(f"Unknown legacy agent labels; repair these records before upgrading: {unknown!r}")

    projects = op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    agents = op.create_table(
        "agents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    revisions = op.create_table(
        "agent_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("agent_id", "number"),
    )
    op.create_index("ix_agents_project_id", "agents", ["project_id"])
    op.create_index("ix_agent_revisions_project_id", "agent_revisions", ["project_id"])
    stamp = datetime.now(UTC).isoformat()
    connection.execute(
        projects.insert(),
        {
            "id": "synthetic-demo",
            "name": "Synthetic Demo",
            "description": "Migrated local synthetic workspace",
            "archived": False,
            "created_at": stamp,
        },
    )
    connection.execute(
        agents.insert(),
        {
            "id": "synthetic-customer-lookup",
            "project_id": "synthetic-demo",
            "name": "Synthetic Customer Lookup",
            "description": "Synthetic read-only demo agent",
            "archived": False,
            "created_at": stamp,
        },
    )
    for number, label in enumerate(("buggy", "fixed"), 1):
        # An executable compatibility mapping, never a reconstructed historical spec.
        # API spec_provenance derives mapping_only from legacy=True without rewriting evidence.
        spec = AgentSpec(variant=label, artifact="goldenloop-demo-agent==0.2.0")
        connection.execute(
            revisions.insert(),
            {
                "id": "synthetic-" + label,
                "agent_id": "synthetic-customer-lookup",
                "project_id": "synthetic-demo",
                "number": number,
                "label": label,
                "spec": spec.model_dump(mode="json"),
                "spec_hash": agent_spec_hash(spec),
                "created_at": stamp,
                "legacy": True,
            },
        )

    for table in ("cases", "imports", "dataset_releases", "chat_sessions", "evaluation_runs"):
        # SQLite permits an additive REFERENCES column only with a NULL default.
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN project_id VARCHAR REFERENCES projects(id)"
        )
        connection.exec_driver_sql(f"UPDATE {table} SET project_id='synthetic-demo'")
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])
    for table in ("chat_sessions", "evaluation_runs"):
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN agent_revision_id VARCHAR REFERENCES agent_revisions(id)"
        )
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN legacy_workflow BOOLEAN NOT NULL DEFAULT 1"
        )
        connection.exec_driver_sql(f"UPDATE {table} SET agent_revision_id='synthetic-' || agent_revision")

    # No tables reference evaluation_runs. Keep FK enforcement ON throughout its rebuild.
    with op.batch_alter_table(
        "evaluation_runs", recreate="always", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
    ) as batch:
        batch.drop_constraint("uq_evaluation_runs_idempotency_key", type_="unique")
        batch.create_unique_constraint("uq_run_project_key", ["project_id", "idempotency_key"])

    for table in ("cases", "imports", "dataset_releases", "chat_sessions", "evaluation_runs"):
        connection.exec_driver_sql(f"""CREATE TRIGGER {table}_ownership_insert BEFORE INSERT ON {table}
            WHEN NEW.project_id IS NULL BEGIN SELECT RAISE(ABORT, 'project ownership required'); END""")
        connection.exec_driver_sql(f"""CREATE TRIGGER {table}_ownership_update BEFORE UPDATE OF project_id ON {table}
            WHEN NEW.project_id IS NOT OLD.project_id BEGIN SELECT RAISE(ABORT, 'project ownership immutable'); END""")

    checks = {
        "agents": "NEW.project_id IS NULL",
        "agent_revisions": "NEW.number < 1 OR NEW.project_id IS NOT (SELECT project_id FROM agents WHERE id=NEW.agent_id)",
        "release_cases": "(SELECT project_id FROM dataset_releases WHERE id=NEW.release_id) IS NOT (SELECT project_id FROM cases WHERE id=NEW.case_id)",
        "feedback": "NEW.candidate_id IS NOT NULL AND (SELECT project_id FROM chat_sessions WHERE id=NEW.session_id) IS NOT (SELECT project_id FROM cases WHERE id=NEW.candidate_id)",
    }
    for table in ("chat_sessions", "evaluation_runs"):
        checks[table] = """(NEW.agent_revision_id IS NOT NULL AND NEW.project_id IS NOT
            (SELECT project_id FROM agent_revisions WHERE id=NEW.agent_revision_id))
            OR (NEW.legacy_workflow=0 AND (NEW.agent_revision_id IS NULL OR NEW.agent_revision IS NOT NEW.agent_revision_id))
            OR (NEW.legacy_workflow=1 AND (NEW.project_id != 'synthetic-demo' OR NEW.agent_revision NOT IN ('buggy','fixed')))"""
    checks["evaluation_runs"] += (
        " OR NEW.project_id IS NOT (SELECT project_id FROM dataset_releases WHERE id=NEW.release_id)"
    )
    for table, condition in checks.items():
        for action in ("INSERT", "UPDATE"):
            connection.exec_driver_sql(f"""CREATE TRIGGER {table}_scope_{action.lower()} BEFORE {action} ON {table}
                WHEN {condition} BEGIN SELECT RAISE(ABORT, 'inconsistent project scope'); END""")
    connection.exec_driver_sql("""CREATE TRIGGER agent_revisions_immutable BEFORE UPDATE ON agent_revisions
        BEGIN SELECT RAISE(ABORT, 'agent revisions immutable'); END""")
    connection.exec_driver_sql("""CREATE TRIGGER agents_ownership_update BEFORE UPDATE OF project_id ON agents
        WHEN NEW.project_id IS NOT OLD.project_id BEGIN SELECT RAISE(ABORT, 'project ownership immutable'); END""")
    for table in ("chat_sessions", "evaluation_runs"):
        connection.exec_driver_sql(f"""CREATE TRIGGER {table}_pin_immutable BEFORE UPDATE ON {table}
            WHEN NEW.agent_revision_id IS NOT OLD.agent_revision_id
              OR NEW.agent_revision IS NOT OLD.agent_revision OR NEW.legacy_workflow IS NOT OLD.legacy_workflow
            BEGIN SELECT RAISE(ABORT, 'execution revision immutable'); END""")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Project migration failed foreign key validation")


def downgrade():
    raise RuntimeError("Destructive downgrades are not supported; restore a SQLite backup instead")
