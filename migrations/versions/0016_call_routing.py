"""Configurable per-number call menu routing."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'call_routing',
        sa.Column('number_id', sa.String(36), sa.ForeignKey('numbers.id'), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('encrypted_config', sa.Text(), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_call_routing_tenant_id', 'call_routing', ['tenant_id'])


def downgrade():
    op.drop_index('ix_call_routing_tenant_id', table_name='call_routing')
    op.drop_table('call_routing')
