"""True call queues and virtual callback tickets."""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "queue_tickets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("number_id", sa.String(length=36), sa.ForeignKey("numbers.id"), nullable=False),
        sa.Column("call_sid", sa.String(length=40), sa.ForeignKey("calls.sid"), nullable=False),
        sa.Column("customer_id", sa.String(length=36), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="waiting"),
        sa.Column("provider_sid", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("current_destination", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("encrypted_payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("call_sid"),
    )
    op.create_index("ix_queue_tickets_tenant_id", "queue_tickets", ["tenant_id"])
    op.create_index("ix_queue_tickets_call_sid", "queue_tickets", ["call_sid"])
    op.create_index("ix_queue_tickets_customer_id", "queue_tickets", ["customer_id"])
    op.create_index("ix_queue_tickets_queue_name", "queue_tickets", ["queue_name"])
    op.create_index("ix_queue_tickets_status", "queue_tickets", ["status"])
    op.create_index("ix_queue_tickets_entered_at", "queue_tickets", ["entered_at"])
    op.create_index("ix_queue_tickets_next_attempt_at", "queue_tickets", ["next_attempt_at"])


def downgrade():
    op.drop_index("ix_queue_tickets_next_attempt_at", table_name="queue_tickets")
    op.drop_index("ix_queue_tickets_entered_at", table_name="queue_tickets")
    op.drop_index("ix_queue_tickets_status", table_name="queue_tickets")
    op.drop_index("ix_queue_tickets_queue_name", table_name="queue_tickets")
    op.drop_index("ix_queue_tickets_customer_id", table_name="queue_tickets")
    op.drop_index("ix_queue_tickets_call_sid", table_name="queue_tickets")
    op.drop_index("ix_queue_tickets_tenant_id", table_name="queue_tickets")
    op.drop_table("queue_tickets")
