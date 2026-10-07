"""Number porting cases and controlled outbound call requests."""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "port_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="review"),
        sa.Column("provider_reference", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("encrypted_payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "phone"),
    )
    op.create_index("ix_port_requests_tenant_id", "port_requests", ["tenant_id"])

    op.create_table(
        "outbound_call_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("number_id", sa.String(length=36), sa.ForeignKey("numbers.id"), nullable=False),
        sa.Column("request_key", sa.String(length=100), nullable=False),
        sa.Column("destination", sa.String(length=20), nullable=False),
        sa.Column("agent_destination", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("provider_sid", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("error", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "request_key"),
    )
    op.create_index("ix_outbound_call_requests_tenant_id", "outbound_call_requests", ["tenant_id"])
    op.create_index("ix_outbound_call_requests_number_id", "outbound_call_requests", ["number_id"])


def downgrade():
    op.drop_index("ix_outbound_call_requests_number_id", table_name="outbound_call_requests")
    op.drop_index("ix_outbound_call_requests_tenant_id", table_name="outbound_call_requests")
    op.drop_table("outbound_call_requests")
    op.drop_index("ix_port_requests_tenant_id", table_name="port_requests")
    op.drop_table("port_requests")
