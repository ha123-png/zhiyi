"""Add versioned extraction templates.

Revision ID: 0004_template_versions
Revises: 0003_confirmed_data_tables
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_template_versions"
down_revision: str | Sequence[str] | None = "0003_confirmed_data_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("builtin_key", sa.String(length=64), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("source_template_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_template_id"], ["templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("builtin_key"),
    )
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("extra_instructions", sa.Text(), nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("validation_rules_json", sa.Text(), nullable=False),
        sa.Column("output_mapping_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version", name="uq_template_version"),
    )
    op.create_index(
        "ix_template_versions_template_id",
        "template_versions",
        ["template_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_template_versions_template_id", table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_table("templates")
