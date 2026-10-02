"""Durable reminder due times."""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("action_jobs") as batch:
        batch.add_column(sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_action_jobs_due_at", ["due_at"])


def downgrade():
    with op.batch_alter_table("action_jobs") as batch:
        batch.drop_index("ix_action_jobs_due_at")
        batch.drop_column("due_at")
