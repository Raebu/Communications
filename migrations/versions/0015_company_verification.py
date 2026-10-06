"""Private company verification state; no browser database access."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('company_verifications',
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), primary_key=True),
        sa.Column('attempt', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('profile_hash', sa.String(64), nullable=False),
        sa.Column('domain', sa.String(253), nullable=False),
        sa.Column('mailbox', sa.String(254), nullable=False),
        sa.Column('company_number', sa.String(8), nullable=False),
        sa.Column('number_type', sa.String(20), nullable=False),
        sa.Column('encrypted_contact', sa.Text(), nullable=False),
        sa.Column('dns_token', sa.String(100), nullable=False),
        sa.Column('email_sends', sa.Integer(), nullable=False),
        sa.Column('email_token_hash', sa.String(64), nullable=False),
        sa.Column('email_expires_at', sa.DateTime(timezone=True)),
        sa.Column('email_verified', sa.Boolean(), nullable=False),
        sa.Column('dns_verified', sa.Boolean(), nullable=False),
        sa.Column('registry_verified', sa.Boolean(), nullable=False),
        sa.Column('authority_verified', sa.Boolean(), nullable=False),
        sa.Column('identity_session', sa.String(100), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('reason', sa.String(80), nullable=False),
        sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reminders', sa.Integer(), nullable=False),
        sa.Column('last_notified', sa.String(100), nullable=False),
        sa.Column('provider_state', sa.Text(), nullable=False))
    op.create_index('ix_company_verifications_next_check_at', 'company_verifications', ['next_check_at'])
    op.create_table('verified_company_claims',
        sa.Column('company_number', sa.String(8), primary_key=True),
        sa.Column('domain', sa.String(253), nullable=False, unique=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == 'postgresql':
        for name in ('company_verifications', 'verified_company_claims'):
            op.execute(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE {name} FROM PUBLIC')
            for role in ('anon', 'authenticated'):
                op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
                    THEN EXECUTE 'REVOKE ALL ON TABLE {name} FROM {role}'; END IF; END $$""")


def downgrade():
    op.drop_table('verified_company_claims')
    op.drop_table('company_verifications')
