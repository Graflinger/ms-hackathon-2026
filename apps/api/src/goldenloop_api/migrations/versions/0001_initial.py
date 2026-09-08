"""Initial local workbench schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("latest", sa.Integer(), nullable=False),
    )
    op.create_table(
        "case_revisions",
        sa.Column("case_id", sa.String(), sa.ForeignKey("cases.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reviewer", sa.String()),
        sa.Column("reason", sa.String()),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_table(
        "imports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("committed", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "dataset_releases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "release_cases",
        sa.Column("release_id", sa.String(), sa.ForeignKey("dataset_releases.id"), primary_key=True),
        sa.Column("case_id", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "revision"], ["case_revisions.case_id", "case_revisions.revision"]
        ),
    )
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("agent_revision", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("trace_complete", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String()),
    )
    op.create_table(
        "message_commands",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("session_id", "turn"),
    )
    op.create_index("ix_message_commands_session_id", "message_commands", ["session_id"])
    op.create_index("ix_message_commands_status", "message_commands", ["status"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("candidate_id", sa.String(), sa.ForeignKey("cases.id")),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index("ix_feedback_session_id", "feedback", ["session_id"])
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("release_id", sa.String(), sa.ForeignKey("dataset_releases.id"), nullable=False),
        sa.Column("agent_revision", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("gate", sa.String()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("error", sa.String()),
        sa.Column("lineage", sa.JSON(), nullable=False),
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_index("ix_evaluation_runs_release_id", "evaluation_runs", ["release_id"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("stream", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_events_stream", "events", ["stream"])


def downgrade():
    raise RuntimeError("Destructive downgrades are not supported; restore a SQLite backup instead")
