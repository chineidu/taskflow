"""Added indexes to the fields: status and created_at

Revision ID: c83b4d0f50af
Revises: 084dd6c092f2
Create Date: 2026-01-01 21:25:26.217472

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c83b4d0f50af'
down_revision: Union[str, Sequence[str], None] = '084dd6c092f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
