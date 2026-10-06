"""human request AI draft

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29 12:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('human_requests', sa.Column('draft', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_constraint('human_request_answered_by_valid', 'human_requests', type_='check')
    op.create_check_constraint('human_request_answered_by_valid', 'human_requests',
                               "answered_by IN ('human', 'llm', 'draft')")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE human_requests SET answered_by = 'human' WHERE answered_by = 'draft'")
    op.drop_constraint('human_request_answered_by_valid', 'human_requests', type_='check')
    op.create_check_constraint('human_request_answered_by_valid', 'human_requests', "answered_by IN ('human', 'llm')")
    op.drop_column('human_requests', 'draft')
