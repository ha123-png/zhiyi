"""Frozen smart pools and per-file autonomous extraction schemas."""
from alembic import op
import sqlalchemy as sa

revision = "0042_autonomous_contract"
down_revision = "0041_template_contract"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    op.add_column("tasks", sa.Column("template_pool_json", sa.Text, nullable=True))
    op.add_column("extractions", sa.Column("output_schema_json", sa.Text, nullable=True))
    op.add_column("data_tables", sa.Column("collection_kind", sa.String(16), nullable=False, server_default="table"))


def downgrade():
    raise RuntimeError("自主提取按文件保存不同字段，请用升级前完整备份恢复旧版本。")
