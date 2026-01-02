"""Initial tasks table creation

Revision ID: 084dd6c092f2
Revises: 246b08e7d055
Create Date: 2026-01-01 21:19:34.792294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '084dd6c092f2'
down_revision: Union[str, Sequence[str], None] = '246b08e7d055'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
