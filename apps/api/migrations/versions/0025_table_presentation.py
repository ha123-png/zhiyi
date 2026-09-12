"""Snapshot presentation when creating a standalone table from a template."""
from alembic import op
import sqlalchemy as sa

revision = "0025_table_presentation"
down_revision = "0024_template_behavior"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_tables", sa.Column("presentation_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_tables", "presentation_json")
