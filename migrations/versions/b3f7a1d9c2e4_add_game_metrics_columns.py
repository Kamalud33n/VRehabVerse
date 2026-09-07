"""add game_type and game_metrics columns

Revision ID: b3f7a1d9c2e4
Revises: a1e9f2c7d4b6
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f7a1d9c2e4'
down_revision: Union[str, Sequence[str], None] = 'a1e9f2c7d4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('sessions', sa.Column('game_type', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_sessions_game_type'), 'sessions', ['game_type'], unique=False)
    op.add_column('sessions', sa.Column('game_metrics', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sessions', 'game_metrics')
    op.drop_index(op.f('ix_sessions_game_type'), table_name='sessions')
    op.drop_column('sessions', 'game_type')