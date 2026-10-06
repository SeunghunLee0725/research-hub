"""agent review by default, and record which model answered

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30 06:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('human_requests', sa.Column('answered_by_model', sa.String(length=64), nullable=True))
    op.alter_column('projects', 'auto_ai_review', server_default=sa.text('true'))
    op.execute("UPDATE projects SET auto_ai_review = true")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('projects', 'auto_ai_review', server_default=sa.text('false'))
    op.drop_column('human_requests', 'answered_by_model')
