"""Add context_length and temperature to model profile versions.

Revision ID: 0016_model_profile_context
Revises: 0015_manual_table_rows
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0016_model_profile_context"
down_revision: str | Sequence[str] | None = "0015_manual_table_rows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 模型方案新增上下文长度（默认 4096）与可选温度；任务快照同步携带，
    # 保证任务处理时使用入队时刻的方案设置。原生 ADD COLUMN，不重建表，
    # 避免 0014 遇到的 SQLite 外键重建问题。
    op.add_column(
        "model_profile_versions",
        sa.Column("context_length", sa.Integer(), nullable=False, server_default="4096"),
    )
    op.add_column(
        "model_profile_versions",
        sa.Column("temperature", sa.Float(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("model_context_length", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("model_temperature", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "model_temperature")
    op.drop_column("tasks", "model_context_length")
    op.drop_column("model_profile_versions", "temperature")
    op.drop_column("model_profile_versions", "context_length")
