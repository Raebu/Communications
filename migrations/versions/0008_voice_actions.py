"""Call-bound action confirmation."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("voice_sessions") as batch:
        batch.add_column(sa.Column("pending_action_id", sa.String(36), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("voice_sessions") as batch:
        batch.drop_column("pending_action_id")
