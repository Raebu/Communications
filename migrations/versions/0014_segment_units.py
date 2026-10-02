"""Keep usage reservations independent of redacted content."""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("segment_units", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("segment_units")
