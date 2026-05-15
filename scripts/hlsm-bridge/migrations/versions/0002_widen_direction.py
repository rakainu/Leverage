"""widen fills.direction to varchar(32)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("fills", "direction", type_=sa.String(32))


def downgrade() -> None:
    op.alter_column("fills", "direction", type_=sa.String(16))
