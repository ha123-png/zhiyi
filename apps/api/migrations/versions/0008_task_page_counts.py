"""Add task page counts for queue resource admission.

Revision ID: 0008_task_page_counts
Revises: 0007_data_row_versions_and_views
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_task_page_counts"
down_revision: str | Sequence[str] | None = "0007_data_row_versions_and_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column(
                "page_count",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("page_count")
