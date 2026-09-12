"""Record current-version switches without duplicating template definitions."""
from alembic import op
import sqlalchemy as sa

revision = "0031_template_restorations"
down_revision = "0030_row_input_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "template_restorations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("template_id", sa.String(36), sa.ForeignKey("templates.id"), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_template_restorations_template_id", "template_restorations", ["template_id"])


def downgrade() -> None:
    op.drop_table("template_restorations")
