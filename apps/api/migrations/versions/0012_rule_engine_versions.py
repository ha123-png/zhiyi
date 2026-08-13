"""Record the rule engine used for each persisted validation.

Revision ID: 0012_rule_engine_versions
Revises: 0011_template_archival
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_rule_engine_versions"
down_revision: str | Sequence[str] | None = "0011_template_archival"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("extractions", "review_revisions"):
        op.add_column(
            table,
            sa.Column(
                "rule_engine_version",
                sa.String(length=32),
                nullable=False,
                server_default="legacy-unversioned",
            ),
        )


def downgrade() -> None:
    for table in ("review_revisions", "extractions"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("rule_engine_version")
