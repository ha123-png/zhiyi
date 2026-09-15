"""Local archive intent and private native-picker source identity."""
from alembic import op
import sqlalchemy as sa

revision = "0039_original_archive"
down_revision = "0038_growth_and_streaming"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("source_file_json", sa.Text, nullable=True))
    op.add_column("template_local_bindings", sa.Column("mode", sa.String(8), nullable=False, server_default="copy"))


def downgrade():
    op.drop_column("template_local_bindings", "mode")
    op.drop_column("tasks", "source_file_json")
