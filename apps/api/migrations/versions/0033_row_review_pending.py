"""Preserve pending-review information alongside warehouse rows."""
from alembic import op
import sqlalchemy as sa

revision = "0033_row_review_pending"
down_revision = "0032_task_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_rows", sa.Column("review_pending", sa.Boolean(), nullable=True))
    op.execute("""UPDATE data_rows SET review_pending = CASE
        WHEN (SELECT status FROM tasks WHERE tasks.id = data_rows.task_id) = 'completed' THEN 0
        ELSE 1 END WHERE task_id IN (SELECT id FROM tasks)""")


def downgrade() -> None:
    op.drop_column("data_rows", "review_pending")
