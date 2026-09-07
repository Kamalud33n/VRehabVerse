"""add email verification

Revision ID: a1e9f2c7d4b6
Revises: cf2ce73436ff
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1e9f2c7d4b6'
down_revision: Union[str, Sequence[str], None] = 'cf2ce73436ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(), nullable=True))

    op.create_table(
        'email_verification_tokens',
        sa.Column('id', sa.String(50), primary_key=True),
        sa.Column('user_id', sa.String(50), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('email', sa.String(150), nullable=False),
        sa.Column('otp_hash', sa.String(255), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('used', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Backfill: accounts that were already approved (or are super_admins)
    # went through the old flow, which had no email verification step at
    # all. Treat them as verified so this change doesn't lock existing,
    # already-trusted users out of accounts they've been using. Anyone
    # still "pending" must go through the new OTP step before an admin can
    # approve them.
    op.execute(
        "UPDATE users SET email_verified = 1, email_verified_at = NOW() "
        "WHERE status = 'approved' OR role = 'super_admin'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('email_verification_tokens')
    op.drop_column('users', 'email_verified_at')
    op.drop_column('users', 'email_verified')
