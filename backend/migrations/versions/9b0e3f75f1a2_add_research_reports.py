"""add durable research reports

Revision ID: 9b0e3f75f1a2
Revises: 7cee87c1bedf
Create Date: 2026-11-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b0e3f75f1a2"
down_revision: Union[str, Sequence[str], None] = "7cee87c1bedf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cik"], ["companies.cik"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reports_ticker_created",
        "research_reports",
        ["ticker", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_reports_ticker_created", table_name="research_reports")
    op.drop_table("research_reports")
