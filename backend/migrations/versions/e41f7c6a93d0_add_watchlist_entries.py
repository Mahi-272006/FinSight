"""add company watchlist entries

Revision ID: e41f7c6a93d0
Revises: 9b0e3f75f1a2
Create Date: 2026-11-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e41f7c6a93d0"
down_revision: Union[str, Sequence[str], None] = "9b0e3f75f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlist_entries",
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cik"], ["companies.cik"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("cik"),
    )


def downgrade() -> None:
    op.drop_table("watchlist_entries")
