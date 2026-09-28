"""Store file revision detail deltas separately for bounded history reads."""
import json

from alembic import op
import sqlalchemy as sa

revision = "0044_revision_details"
down_revision = "0043_dynamic_groupings"
branch_labels = None
depends_on = None


def _counts(change):
    before, after = change["before"], change["after"]
    if after is None:
        last = change.get("deleted_before")
        modified = 0
        if before is not None and last is not None:
            old, new = before["values"], last["values"]
            modified = sum(old.get(key) != new.get(key) for key in old.keys() | new.keys())
        return modified, 1, 0
    if before is None:
        return 0, 0, 1
    old, new = before["values"], after["values"]
    return sum(old.get(key) != new.get(key) for key in old.keys() | new.keys()), 0, 0


def upgrade():
    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    for name in ("modified_fields", "deleted_details", "added_details", "total_details"):
        op.add_column("file_revisions", sa.Column(name, sa.Integer, nullable=False, server_default="0"))
    op.create_table("file_revision_details",
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("file_revisions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column("item_index", sa.Integer, nullable=False),
        sa.Column("change_json", sa.Text, nullable=False))
    op.create_index("ix_file_revision_detail_order", "file_revision_details", ["revision_id", "item_index", "row_id"])
    # This transforms valid file-level history only. It neither replays old
    # extraction/review copies into current facts nor invents prior revisions.
    for record in connection.execute(sa.text("SELECT id, changes_json FROM file_revisions")).mappings():
        changes = json.loads(record["changes_json"])
        items = changes.pop("items", {})
        modified, deleted, added = len(changes["header"]), 0, 0
        batch = []
        for row_id, change in items.items():
            counts = _counts(change)
            modified, deleted, added = modified + counts[0], deleted + counts[1], added + counts[2]
            item = change["after"] or change.get("deleted_before") or change["before"]
            batch.append({"revision_id": record["id"], "row_id": int(row_id), "item_index": item["item_index"],
                          "change_json": json.dumps(change, ensure_ascii=False, separators=(",", ":"))})
            if len(batch) == 200:
                _insert(connection, batch)
                batch = []
        if batch:
            _insert(connection, batch)
        changes["items"] = {}
        connection.execute(sa.text("UPDATE file_revisions SET changes_json=:changes, modified_fields=:modified, "
            "deleted_details=:deleted, added_details=:added, total_details=:total WHERE id=:id"),
            {"id": record["id"], "changes": json.dumps(changes, ensure_ascii=False, separators=(",", ":")),
             "modified": modified, "deleted": deleted, "added": added, "total": len(items)})


def _insert(connection, batch):
    connection.execute(sa.text("INSERT INTO file_revision_details (revision_id,row_id,item_index,change_json) "
        "VALUES (:revision_id,:row_id,:item_index,:change_json)"), batch)


def downgrade():
    raise RuntimeError("请通过完整备份恢复旧版本，不能丢弃文件修订明细。")
