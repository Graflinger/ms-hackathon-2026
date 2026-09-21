"""Pin playground execution mode; historical sessions remain mock."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_sessions", sa.Column("mode", sa.String(), nullable=False, server_default="mock"))
    connection = op.get_bind()
    connection.exec_driver_sql("""CREATE TRIGGER chat_sessions_mode_immutable BEFORE UPDATE OF mode ON chat_sessions
        WHEN NEW.mode IS NOT OLD.mode BEGIN SELECT RAISE(ABORT, 'chat mode immutable'); END""")
    connection.exec_driver_sql("""CREATE TRIGGER chat_sessions_mode_insert BEFORE INSERT ON chat_sessions
        WHEN NEW.mode NOT IN ('mock', 'live') OR (NEW.legacy_workflow=1 AND NEW.mode!='mock')
        BEGIN SELECT RAISE(ABORT, 'invalid chat mode'); END""")


def downgrade():
    raise RuntimeError("Downgrade is unsupported; restore a backup instead")
