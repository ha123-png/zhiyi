"""Add immutable model profiles and a single active pointer.

Revision ID: 0014_model_profiles
Revises: 0013_task_model_snapshots
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0014_model_profiles"
down_revision: str | Sequence[str] | None = "0013_task_model_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_profile_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=32), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("secret_ref", sa.String(length=128), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "remote_data_acknowledged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["model_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "version", name="uq_model_profile_version"),
    )
    op.create_index(
        "ix_model_profile_versions_profile_id",
        "model_profile_versions",
        ["profile_id"],
    )
    op.create_table(
        "model_runtime_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("active_profile_id", sa.String(length=36), nullable=True),
        sa.Column("active_profile_version", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["active_profile_id"], ["model_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_model_runtime_state_singleton"),
        sa.CheckConstraint(
            "(active_profile_id IS NULL AND active_profile_version IS NULL) OR "
            "(active_profile_id IS NOT NULL AND active_profile_version IS NOT NULL)",
            name="ck_model_runtime_state_complete_pointer",
        ),
    )
    # tasks 是其他表（extractions/review_revisions/data_rows 等）外键引用的父表。
    # 用 batch_alter_table 重建会在 PRAGMA foreign_keys=ON 且有子行数据时 DROP 失败，
    # 而 SQLite 不允许在迁移事务内临时关闭外键（PRAGMA 在事务中是 no-op）。
    # 因此改用原生 ADD COLUMN 加两个可空列，不重建表；DB 层不加外键约束，
    # 模型（orm）层的 ForeignKey 保留，应用层关系不受影响。
    op.add_column(
        "tasks",
        sa.Column("model_profile_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("model_profile_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    # SQLite 下 drop_column 会走 batch 重建 tasks；若子表已有数据可能因外键失败，
    # 这是 SQLite 迁移的固有限制，回滚路径需在无关键子数据或人工介入时使用。
    op.drop_column("tasks", "model_profile_version")
    op.drop_column("tasks", "model_profile_id")
    op.drop_table("model_runtime_state")
    op.drop_index(
        "ix_model_profile_versions_profile_id",
        table_name="model_profile_versions",
    )
    op.drop_table("model_profile_versions")
    op.drop_table("model_profiles")
