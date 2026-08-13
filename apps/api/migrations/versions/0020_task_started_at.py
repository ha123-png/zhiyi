"""Task processing start timestamp for per-attempt timing.

Revision ID: 0020_task_started_at
Revises: 0019_template_smart_pool
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0020_task_started_at"
down_revision: str | Sequence[str] | None = "0019_template_smart_pool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 本次处理尝试的开始时间：上传时等于创建时间，重试/恢复/选模板后重置，
    # 前端计时据此从零重新计算（旧数据回退用 created_at）。
    op.add_column(
        "tasks",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 存量任务：用 created_at 兜底，保证历史计时仍可用
    op.execute("UPDATE tasks SET started_at = created_at WHERE started_at IS NULL")


def downgrade() -> None:
    op.drop_column("tasks", "started_at")
