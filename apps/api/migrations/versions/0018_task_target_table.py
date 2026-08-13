"""Allow tasks to target a specific data table instead of the template table.

Revision ID: 0018_task_target_table
Revises: 0017_system_settings
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018_task_target_table"
down_revision: str | Sequence[str] | None = "0017_system_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 提取可指定目标数据表（默认 NULL = 模板唯一表）。
    # 普通字符串列（不设外键）：目标表被删除后，物化时检测并回退到模板表重建。
    op.add_column(
        "tasks",
        sa.Column("target_table_id", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "target_table_id")
