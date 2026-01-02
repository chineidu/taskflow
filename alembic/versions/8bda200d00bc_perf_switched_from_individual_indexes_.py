"""[perf] Switched from individual indexes to composite index

Revision ID: 8bda200d00bc
Revises: c83b4d0f50af
Create Date: 2026-01-02 01:31:08.213561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8bda200d00bc'
down_revision: Union[str, Sequence[str], None] = 'c83b4d0f50af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
