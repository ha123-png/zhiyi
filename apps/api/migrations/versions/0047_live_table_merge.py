"""Store ordered source table IDs for live merges."""
from alembic import op
import sqlalchemy as sa

revision = "0047_live_table_merge"
down_revision = "0046_upload_intent"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("data_tables", sa.Column("merge_sources_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("data_tables", "merge_sources_json")
