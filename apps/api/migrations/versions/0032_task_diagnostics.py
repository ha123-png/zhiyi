"""Keep bounded task diagnostics separate from the lightweight status summary."""
from alembic import op
import sqlalchemy as sa

revision = "0032_task_diagnostics"
down_revision = "0031_template_restorations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("failure_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "failure_detail")
