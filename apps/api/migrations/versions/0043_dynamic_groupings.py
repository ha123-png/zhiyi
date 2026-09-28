"""Keep grouping rules independently of the categories currently present."""
import json

from alembic import op
import sqlalchemy as sa

revision = "0043_dynamic_groupings"
down_revision = "0042_autonomous_contract"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    op.add_column("data_tables", sa.Column("grouping_json", sa.Text, nullable=False, server_default="[]"))
    rules = {}
    for table_id, field_key in connection.exec_driver_sql("SELECT DISTINCT table_id, field_key FROM data_views ORDER BY table_id, field_key"):
        rules.setdefault(table_id, []).append(field_key)
    for table_id, fields in rules.items():
        connection.execute(sa.text("UPDATE data_tables SET grouping_json=:rules WHERE id=:id"),
                           {"id": table_id, "rules": json.dumps(fields, ensure_ascii=False)})
    # This old bucket mixed every unlinked record. Rebuild it by actual source
    # groups; old references become unavailable instead of silently widening.
    connection.exec_driver_sql("DELETE FROM data_views WHERE field_key='' AND field_value_json='null'")


def downgrade():
    raise RuntimeError("分组规则已独立保存，请使用升级前备份恢复旧版本。")
