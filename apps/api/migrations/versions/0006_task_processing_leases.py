"""Add task processing leases and persistent failure details.

Revision ID: 0006_task_processing_leases
Revises: 0005_task_template_snapshots
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_task_processing_leases"
down_revision: str | Sequence[str] | None = "0005_task_template_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("lease_token", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("failure_code", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("failure_message", sa.String(length=512), nullable=True)
        )
        batch.create_index("ix_tasks_lease_token", ["lease_token"], unique=False)
        batch.create_index(
            "ix_tasks_lease_expires_at",
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_lease_expires_at")
        batch.drop_index("ix_tasks_lease_token")
        batch.drop_column("failure_message")
        batch.drop_column("failure_code")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_token")
        batch.drop_column("attempt_count")
