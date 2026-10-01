"""Tenant AI settings, durable drafts and bounded voice sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_profiles",
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("voice_enabled", sa.Boolean(), nullable=False),
        sa.Column("business_info", sa.Text(), nullable=False),
        sa.Column("greeting", sa.String(500), nullable=False),
    )
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reply", sa.Text(), nullable=False),
        sa.Column("error", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "voice_sessions",
        sa.Column("sid", sa.String(40), sa.ForeignKey("calls.sid"), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("history", sa.Text(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("previous_token", sa.String(64), nullable=False),
        sa.Column("last_xml", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
    )


def downgrade():
    for name in ("voice_sessions", "ai_jobs", "ai_profiles"):
        op.drop_table(name)
