"""Keep original names immutable and store separately confirmed display names."""
from alembic import op
import sqlalchemy as sa

revision = "0028_file_names"
down_revision = "0027_local_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("file_name_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "file_name_json")
