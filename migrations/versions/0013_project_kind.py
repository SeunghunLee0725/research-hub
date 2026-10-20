"""project kind (research grant vs short project) and optional period

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-20 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('kind', sa.String(16), nullable=False, server_default='research'))
    op.create_check_constraint('project_kind_valid', 'projects', "kind IN ('research', 'short')")
    op.add_column('projects', sa.Column('starts_on', sa.Date(), nullable=True))
    op.add_column('projects', sa.Column('ends_on', sa.Date(), nullable=True))
    op.create_check_constraint('project_period_order', 'projects',
                               'starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('project_period_order', 'projects', type_='check')
    op.drop_column('projects', 'ends_on')
    op.drop_column('projects', 'starts_on')
    op.drop_constraint('project_kind_valid', 'projects', type_='check')
    op.drop_column('projects', 'kind')
