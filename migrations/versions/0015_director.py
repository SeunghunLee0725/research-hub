"""project director: a research charter and the director's decisions at each task gate

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-25 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KINDS = ('plan', 'implement', 'run', 'review', 'analyze', 'verify', 'report', 'approve', 'diagnose')
NEW_KINDS = OLD_KINDS + ('charter', 'direct')


def _kinds(values: tuple[str, ...]) -> str:
    return f"kind IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('director_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('projects', sa.Column('charter', postgresql.JSONB(), nullable=True))
    op.add_column('projects', sa.Column('charter_confirmed_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint('step_kind_valid', 'steps', type_='check')
    op.create_check_constraint('step_kind_valid', 'steps', _kinds(NEW_KINDS))
    op.create_table(
        'director_decisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('step_id', sa.Integer(), sa.ForeignKey('steps.id', ondelete='SET NULL'), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('payload', postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('model', sa.String(64), nullable=True),
        sa.Column('answer', postgresql.JSONB(), nullable=True),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('applied', 'pending', 'answered')", name='director_status_valid'),
    )
    op.create_index('director_decisions_project_at', 'director_decisions', ['project_id', 'at'])
    # Every project that exists today is handed to the director (researcher's decision, 2026-10-25).
    op.execute("UPDATE projects SET director_enabled = true WHERE closed_at IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('director_decisions_project_at', 'director_decisions')
    op.drop_table('director_decisions')
    op.execute(f"DELETE FROM steps WHERE kind IN ('charter', 'direct')")
    op.drop_constraint('step_kind_valid', 'steps', type_='check')
    op.create_check_constraint('step_kind_valid', 'steps', _kinds(OLD_KINDS))
    op.drop_column('projects', 'charter_confirmed_at')
    op.drop_column('projects', 'charter')
    op.drop_column('projects', 'director_enabled')
