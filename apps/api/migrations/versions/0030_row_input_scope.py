"""Preserve source input coverage when history is removed or tables are merged."""
from alembic import op
import sqlalchemy as sa

revision = "0030_row_input_scope"
down_revision = "0029_internal_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_rows", sa.Column("input_scope_json", sa.Text(), nullable=True))
    op.execute("UPDATE data_rows SET input_scope_json = (SELECT input_scope_json FROM extractions WHERE extractions.task_id = data_rows.task_id) WHERE task_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("data_rows", "input_scope_json")
