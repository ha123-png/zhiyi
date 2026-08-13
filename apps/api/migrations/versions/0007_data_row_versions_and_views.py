"""Add stable row versions, row audit history, and split views.

Revision ID: 0007_data_row_versions_and_views
Revises: 0006_task_processing_leases
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_data_row_versions_and_views"
down_revision: str | Sequence[str] | None = "0006_task_processing_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("data_rows") as batch:
        batch.add_column(
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )

    op.create_table(
        "data_row_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("table_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("editor", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_data_row_revisions_row_id",
        "data_row_revisions",
        ["row_id"],
    )
    op.create_index(
        "ix_data_row_revisions_table_id",
        "data_row_revisions",
        ["table_id"],
    )
    op.create_index(
        "ix_data_row_revisions_task_id",
        "data_row_revisions",
        ["task_id"],
    )

    op.create_table(
        "data_views",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("table_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("field_key", sa.String(length=128), nullable=False),
        sa.Column("field_value_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["table_id"], ["data_tables.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "table_id",
            "field_key",
            "field_value_json",
            name="uq_data_view_filter",
        ),
    )
    op.create_index("ix_data_views_table_id", "data_views", ["table_id"])


def downgrade() -> None:
    op.drop_index("ix_data_views_table_id", table_name="data_views")
    op.drop_table("data_views")
    op.drop_index(
        "ix_data_row_revisions_task_id",
        table_name="data_row_revisions",
    )
    op.drop_index(
        "ix_data_row_revisions_table_id",
        table_name="data_row_revisions",
    )
    op.drop_index(
        "ix_data_row_revisions_row_id",
        table_name="data_row_revisions",
    )
    op.drop_table("data_row_revisions")
    with op.batch_alter_table("data_rows") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("row_version")
