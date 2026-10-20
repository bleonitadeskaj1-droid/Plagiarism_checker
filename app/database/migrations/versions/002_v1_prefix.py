"""Rename v1 tables to avoid legacy schema collision.

Migration 001 already creates v1_* tables. This revision is kept for history
and performs no changes on fresh installs.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002_v1_prefix"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
