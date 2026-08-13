"""Allow manual table rows without a source task.

Revision ID: 0015_manual_table_rows
Revises: 0014_model_profiles
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0015_manual_table_rows"
down_revision: str | Sequence[str] | None = "0014_model_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 手动新建/合并表需要存储列定义（中文列名契约的来源）。
    op.add_column(
        "data_tables",
        sa.Column("columns_json", sa.Text(), nullable=True),
    )
    # data_rows 是被引用方（task_id/table_id 外键指向 tasks/data_tables），
    # 不被其他表外键引用，batch 重建安全；重建仅将 task_id 改为可空，
    # 以支持手动新增的无来源任务行。SQLite UNIQUE 中 NULL 互不冲突，
    # 因此 (task_id, item_index) 唯一约束仍可让多行手动数据共存。
    with op.batch_alter_table("data_rows") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
    # data_row_revisions 记录手动行的变更，task_id 同样允许为空。
    with op.batch_alter_table("data_row_revisions") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("data_row_revisions") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
    with op.batch_alter_table("data_rows") as batch:
        batch.alter_column(
            "task_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
    op.drop_column("data_tables", "columns_json")
