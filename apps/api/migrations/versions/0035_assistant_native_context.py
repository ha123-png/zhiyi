"""Preserve completed native message segments for stable conversational prefixes."""
from alembic import op
import sqlalchemy as sa

revision = "0035_assistant_native_context"
down_revision = "0034_assistant"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistant_messages", sa.Column("native_context_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("assistant_messages", "native_context_json")
