"""diagnose a stuck task and let the researcher choose the fix

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04 09:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('auto_diagnose', sa.Boolean(), nullable=False,
                                        server_default=sa.text('true')))
    op.drop_constraint('step_kind_valid', 'steps', type_='check')
    op.create_check_constraint(
        'step_kind_valid', 'steps',
        "kind IN ('plan', 'implement', 'run', 'review', 'analyze', 'verify', 'report', 'approve', 'diagnose')")
    op.drop_constraint('approval_kind_valid', 'approvals', type_='check')
    op.create_check_constraint('approval_kind_valid', 'approvals', "kind IN ('start', 'result', 'fix')")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM approvals WHERE kind = 'fix'")
    op.execute("DELETE FROM steps WHERE kind = 'diagnose'")
    op.drop_constraint('approval_kind_valid', 'approvals', type_='check')
    op.create_check_constraint('approval_kind_valid', 'approvals', "kind IN ('start', 'result')")
    op.drop_constraint('step_kind_valid', 'steps', type_='check')
    op.create_check_constraint(
        'step_kind_valid', 'steps',
        "kind IN ('plan', 'implement', 'run', 'review', 'analyze', 'verify', 'report', 'approve')")
    op.drop_column('projects', 'auto_diagnose')
