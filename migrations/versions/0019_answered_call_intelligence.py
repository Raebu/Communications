"""Answered-call intelligence consent."""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'intelligence_profiles',
        sa.Column('analyse_calls', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column('intelligence_profiles', 'analyse_calls')
