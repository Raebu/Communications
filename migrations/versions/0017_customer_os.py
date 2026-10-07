"""Customer Communication OS core."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'customer_states',
        sa.Column('customer_id', sa.String(36), sa.ForeignKey('customers.id'), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('encrypted_state', sa.Text(), nullable=False, server_default=''),
        sa.Column('risk_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('revenue_signal', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('vip', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('owner', sa.String(100), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_customer_states_tenant_id', 'customer_states', ['tenant_id'])

    op.create_table(
        'customer_identities',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('customer_id', sa.String(36), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('identity_hash', sa.String(64), nullable=False),
        sa.Column('encrypted_value', sa.Text(), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'kind', 'identity_hash'),
    )
    op.create_index('ix_customer_identities_tenant_id', 'customer_identities', ['tenant_id'])
    op.create_index('ix_customer_identities_customer_id', 'customer_identities', ['customer_id'])

    op.create_table(
        'customer_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('customer_id', sa.String(36), sa.ForeignKey('customers.id')),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('source_id', sa.String(100), nullable=False, server_default=''),
        sa.Column('encrypted_payload', sa.Text(), nullable=False, server_default=''),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_customer_events_tenant_id', 'customer_events', ['tenant_id'])
    op.create_index('ix_customer_events_customer_id', 'customer_events', ['customer_id'])
    op.create_index('ix_customer_events_kind', 'customer_events', ['kind'])
    op.create_index('ix_customer_events_occurred_at', 'customer_events', ['occurred_at'])

    op.create_table(
        'outcomes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('customer_id', sa.String(36), sa.ForeignKey('customers.id')),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id')),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('owner', sa.String(100), nullable=False, server_default=''),
        sa.Column('due_at', sa.DateTime(timezone=True)),
        sa.Column('encrypted_payload', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_outcomes_tenant_id', 'outcomes', ['tenant_id'])
    op.create_index('ix_outcomes_customer_id', 'outcomes', ['customer_id'])
    op.create_index('ix_outcomes_status', 'outcomes', ['status'])
    op.create_index('ix_outcomes_due_at', 'outcomes', ['due_at'])

    op.create_table(
        'promises',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('customer_id', sa.String(36), sa.ForeignKey('customers.id')),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id')),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('owner', sa.String(100), nullable=False, server_default=''),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('encrypted_commitment', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_promises_tenant_id', 'promises', ['tenant_id'])
    op.create_index('ix_promises_customer_id', 'promises', ['customer_id'])
    op.create_index('ix_promises_status', 'promises', ['status'])
    op.create_index('ix_promises_due_at', 'promises', ['due_at'])

    op.create_table(
        'recovery_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('customer_id', sa.String(36), sa.ForeignKey('customers.id')),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('encrypted_payload', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_recovery_jobs_tenant_id', 'recovery_jobs', ['tenant_id'])
    op.create_index('ix_recovery_jobs_customer_id', 'recovery_jobs', ['customer_id'])
    op.create_index('ix_recovery_jobs_status', 'recovery_jobs', ['status'])
    op.create_index('ix_recovery_jobs_due_at', 'recovery_jobs', ['due_at'])

    op.create_table(
        'guarantee_rules',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('event_kind', sa.String(50), nullable=False),
        sa.Column('max_minutes', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(30), nullable=False, server_default='alert'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_guarantee_rules_tenant_id', 'guarantee_rules', ['tenant_id'])


def downgrade():
    op.drop_index('ix_guarantee_rules_tenant_id', table_name='guarantee_rules')
    op.drop_table('guarantee_rules')
    op.drop_index('ix_recovery_jobs_due_at', table_name='recovery_jobs')
    op.drop_index('ix_recovery_jobs_status', table_name='recovery_jobs')
    op.drop_index('ix_recovery_jobs_customer_id', table_name='recovery_jobs')
    op.drop_index('ix_recovery_jobs_tenant_id', table_name='recovery_jobs')
    op.drop_table('recovery_jobs')
    op.drop_index('ix_promises_due_at', table_name='promises')
    op.drop_index('ix_promises_status', table_name='promises')
    op.drop_index('ix_promises_customer_id', table_name='promises')
    op.drop_index('ix_promises_tenant_id', table_name='promises')
    op.drop_table('promises')
    op.drop_index('ix_outcomes_due_at', table_name='outcomes')
    op.drop_index('ix_outcomes_status', table_name='outcomes')
    op.drop_index('ix_outcomes_customer_id', table_name='outcomes')
    op.drop_index('ix_outcomes_tenant_id', table_name='outcomes')
    op.drop_table('outcomes')
    op.drop_index('ix_customer_events_occurred_at', table_name='customer_events')
    op.drop_index('ix_customer_events_kind', table_name='customer_events')
    op.drop_index('ix_customer_events_customer_id', table_name='customer_events')
    op.drop_index('ix_customer_events_tenant_id', table_name='customer_events')
    op.drop_table('customer_events')
    op.drop_index('ix_customer_identities_customer_id', table_name='customer_identities')
    op.drop_index('ix_customer_identities_tenant_id', table_name='customer_identities')
    op.drop_table('customer_identities')
    op.drop_index('ix_customer_states_tenant_id', table_name='customer_states')
    op.drop_table('customer_states')
