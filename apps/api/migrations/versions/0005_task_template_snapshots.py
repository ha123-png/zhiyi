"""Bind tasks and extractions to exact template versions.

Revision ID: 0005_task_template_snapshots
Revises: 0004_template_versions
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_task_template_snapshots"
down_revision: str | Sequence[str] | None = "0004_template_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("template_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("template_version", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "candidate_templates_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
        batch.create_foreign_key(
            "fk_tasks_template_id",
            "templates",
            ["template_id"],
            ["id"],
        )
    with op.batch_alter_table("extractions") as batch:
        batch.add_column(sa.Column("template_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("template_version", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_extractions_template_id",
            "templates",
            ["template_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("extractions") as batch:
        batch.drop_constraint("fk_extractions_template_id", type_="foreignkey")
        batch.drop_column("template_version")
        batch.drop_column("template_id")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_template_id", type_="foreignkey")
        batch.drop_column("candidate_templates_json")
        batch.drop_column("template_version")
        batch.drop_column("template_id")
