"""Preserve upload intent for atomic duplicate protection."""
from alembic import op
import sqlalchemy as sa

revision = "0046_upload_intent"
down_revision = "0045_assistant_authorization"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("upload_intent_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("tasks", "upload_intent_json")
