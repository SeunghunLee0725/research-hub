"""a finished project is closed: no new work, its records and workdir stay

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('projects', sa.Column('closed_note', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'closed_note')
    op.drop_column('projects', 'closed_at')
