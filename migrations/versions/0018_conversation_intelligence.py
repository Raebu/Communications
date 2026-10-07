"""Opt-in conversation intelligence."""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'intelligence_profiles',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('analyse_voicemail', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('analyse_messages', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('retention_days', sa.Integer(), nullable=False, server_default='90'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'intelligence_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('event_id', sa.String(36), sa.ForeignKey('customer_events.id'), nullable=False, unique=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('result', sa.Text(), nullable=False, server_default=''),
        sa.Column('error', sa.String(80), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_intelligence_jobs_tenant_id', 'intelligence_jobs', ['tenant_id'])
    op.create_index('ix_intelligence_jobs_event_id', 'intelligence_jobs', ['event_id'])
    op.create_index('ix_intelligence_jobs_status', 'intelligence_jobs', ['status'])


def downgrade():
    op.drop_index('ix_intelligence_jobs_status', table_name='intelligence_jobs')
    op.drop_index('ix_intelligence_jobs_event_id', table_name='intelligence_jobs')
    op.drop_index('ix_intelligence_jobs_tenant_id', table_name='intelligence_jobs')
    op.drop_table('intelligence_jobs')
    op.drop_table('intelligence_profiles')
