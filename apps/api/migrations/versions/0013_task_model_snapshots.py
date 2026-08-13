"""Freeze non-secret model configuration on queued tasks.

Revision ID: 0013_task_model_snapshots
Revises: 0012_rule_engine_versions
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_task_model_snapshots"
down_revision: str | Sequence[str] | None = "0012_rule_engine_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column(
            "model_config_version",
            sa.String(length=32),
            nullable=False,
            server_default="legacy-unversioned",
        ),
        sa.Column("model_provider", sa.String(length=32), nullable=True),
        sa.Column("model_base_url", sa.String(length=1024), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("model_reasoning_effort", sa.String(length=32), nullable=True),
        sa.Column("model_timeout_seconds", sa.Float(), nullable=True),
        sa.Column("model_secret_ref", sa.String(length=128), nullable=True),
    )
    for column in columns:
        op.add_column("tasks", column)


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        for column in (
            "model_secret_ref",
            "model_timeout_seconds",
            "model_reasoning_effort",
            "model_name",
            "model_base_url",
            "model_provider",
            "model_config_version",
        ):
            batch.drop_column(column)
