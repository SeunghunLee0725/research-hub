"""the agent decides approvals, and the record names who decided

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30 09:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('approvals', sa.Column('decided_by', sa.String(length=64), nullable=True))
    op.execute("UPDATE approvals SET decided_by = 'human' WHERE status <> 'pending'")
    op.add_column('projects', sa.Column('auto_approve', sa.Boolean(), nullable=False,
                                        server_default=sa.text('true')))
    op.drop_constraint('step_kind_valid', 'steps', type_='check')
    op.create_check_constraint(
        'step_kind_valid', 'steps',
        "kind IN ('plan', 'implement', 'run', 'review', 'analyze', 'verify', 'report', 'approve')")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM steps WHERE kind = 'approve'")
    op.drop_constraint('step_kind_valid', 'steps', type_='check')
    op.create_check_constraint(
        'step_kind_valid', 'steps',
        "kind IN ('plan', 'implement', 'run', 'review', 'analyze', 'verify', 'report')")
    op.drop_column('projects', 'auto_approve')
    op.drop_column('approvals', 'decided_by')
