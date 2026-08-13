"""Add versioned review revisions.

Revision ID: 0002_review_revisions
Revises: 0001_initial
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_review_revisions"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("validation_json", sa.Text(), nullable=False),
        sa.Column("changes_json", sa.Text(), nullable=False),
        sa.Column("editor", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "version",
            name="uq_review_task_version",
        ),
    )
    op.create_index(
        "ix_review_revisions_task_id",
        "review_revisions",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_revisions_task_id",
        table_name="review_revisions",
    )
    op.drop_table("review_revisions")
