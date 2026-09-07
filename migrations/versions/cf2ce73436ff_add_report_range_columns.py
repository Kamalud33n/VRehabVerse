"""add report range columns

Revision ID: cf2ce73436ff
Revises: e16fc4c8ad3d
Create Date: 2026-07-26 09:26:14.739334

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf2ce73436ff'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('reports', sa.Column('period_start', sa.Date(), nullable=True))
    op.add_column('reports', sa.Column('period_end', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reports', 'period_end')
    op.drop_column('reports', 'period_start')