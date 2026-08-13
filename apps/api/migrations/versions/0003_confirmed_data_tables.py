"""Add confirmed documents and logical data tables.

Revision ID: 0003_confirmed_data_tables
Revises: 0002_review_revisions
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_confirmed_data_tables"
down_revision: str | Sequence[str] | None = "0002_review_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_tables",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("template_key", sa.String(length=128), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("document_kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_key",
            "template_version",
            name="uq_data_table_template_version",
        ),
    )
    op.create_table(
        "confirmed_documents",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("table_id", sa.String(length=36), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["data_tables.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_confirmed_documents_table_id",
        "confirmed_documents",
        ["table_id"],
        unique=False,
    )
    op.create_table(
        "data_rows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("table_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("row_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["data_tables.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "item_index", name="uq_data_row_task_item"),
    )
    op.create_index("ix_data_rows_table_id", "data_rows", ["table_id"], unique=False)
    op.create_index("ix_data_rows_task_id", "data_rows", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_data_rows_task_id", table_name="data_rows")
    op.drop_index("ix_data_rows_table_id", table_name="data_rows")
    op.drop_table("data_rows")
    op.drop_index("ix_confirmed_documents_table_id", table_name="confirmed_documents")
    op.drop_table("confirmed_documents")
    op.drop_table("data_tables")
