"""Read-only preflight for the v0.5 current-data migration.

Never choose between disagreeing legacy results. The report contains identities
and counts, not document contents, prompts, credentials or local source paths.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from collections import Counter
from pathlib import Path
from typing import Any


BUILTIN_ITEM_KEYS = {
    "name", "specification", "unit", "quantity", "unit_price", "tax_rate",
    "item_amount", "item_tax_amount", "item_remarks",
}
METADATA_KEYS = {"source_filename", "item_index", "__row_group"}
ITEM_ALIASES = {"amount": "item_amount", "tax_amount": "item_tax_amount", "remarks": "item_remarks"}


def _object(raw: str | None) -> dict[str, Any]:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        raise ValueError("应为 JSON 对象")
    return value


def split_legacy_result(
    raw: str, *, custom: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = _object(raw)
    header = result.get("header", {}) if custom else {
        key: value for key, value in result.items() if key != "items"
    }
    items = result.get("items", [])
    if not isinstance(header, dict) or not isinstance(items, list):
        raise ValueError("文件字段或明细结构无效")
    if any(not isinstance(item, dict) for item in items):
        raise ValueError("明细应为 JSON 对象")
    if not custom:
        items = [{ITEM_ALIASES.get(key, key): value for key, value in item.items()} for item in items]
    return header, items


def _business_values(value: dict[str, Any]) -> dict[str, Any]:
    # Old built-in flattening adds null item cells for header-only documents.
    # Absent and null are equivalent; zero and false must never be discarded.
    return {key: item for key, item in value.items() if key not in METADATA_KEYS and item is not None}


def _same_json(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def audit_connection(connection: sqlite3.Connection) -> dict[str, Any]:
    """Inspect an already-open, consistent snapshot; this function never writes."""
    connection.row_factory = sqlite3.Row
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    revision = None
    if "alembic_version" in tables:
        found = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        revision = found[0][0] if len(found) == 1 else None
    report: dict[str, Any] = {
        "format_version": 1, "schema_revision": revision,
        "read_only": True, "ready": False, "counts": {}, "issues": [],
    }
    required = {"tasks", "extractions", "review_revisions", "data_rows", "data_tables", "confirmed_documents"}
    if not required <= tables:
        report["issues"].append({"code": "unsupported_schema", "severity": "blocker",
                                 "message": "数据库结构不完整，请先验证备份与源版本。"})
        return report
    counts = report["counts"]
    for name in sorted(required):
        counts[name] = connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
    issues = report["issues"]

    def issue(code: str, message: str, **identity: Any) -> None:
        issues.append({"code": code, "severity": "blocker", "message": message, **identity})

    new_tables = {"file_current_data", "data_edit_sessions", "file_revisions", "pending_original_deletions"}
    if new_tables & tables:
        expected = {
            "file_current_data": {"task_id", "table_id", "header_json", "schema_json", "validation_json", "has_data", "current_version", "next_item_index", "updated_at"},
            "file_revisions": {"id", "task_id", "session_id", "version", "changes_json", "schema_json", "sealed_at"},
            "data_edit_sessions": {"id", "owner", "editor", "sealed_at"},
            "pending_original_deletions": {"relative_path", "task_id", "created_at", "attempts", "failure_code"},
            "data_rows": {"id", "table_id", "task_id", "item_index", "row_json", "is_header_only"},
        }
        incomplete = [name for name, fields in expected.items() if name not in tables or not fields <= {
            row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')
        }]
        if incomplete or revision in {None, "0039_original_archive"}:
            issue("incomplete_current_schema", "当前数据迁移未完整完成，请恢复升级前备份后重试。", tables=incomplete)
            return report
        for name, column, identity in (('file_current_data', 'header_json', 'task_id'), ('data_rows', 'row_json', 'task_id')):
            invalid = connection.execute(f'SELECT "{identity}" FROM "{name}" WHERE NOT json_valid("{column}")').fetchall()
            for row in invalid:
                issue('invalid_current_json', '当前数据包含无效结构，请检查备份与数据库。', file_id=row[0])
        if issues:
            return report
        checks = (
            ("orphan_current_file", "当前数据所属的文件不存在。", "SELECT c.task_id FROM file_current_data c LEFT JOIN tasks t ON t.id=c.task_id WHERE t.id IS NULL"),
            ("missing_file_current", "文件有提取历史但缺少唯一当前数据。", "SELECT e.task_id FROM extractions e LEFT JOIN file_current_data c ON c.task_id=e.task_id WHERE c.task_id IS NULL"),
            ("orphan_current_row", "明细缺少唯一当前数据，不能回退读取旧快照。", "SELECT r.task_id FROM data_rows r LEFT JOIN file_current_data c ON c.task_id=r.task_id WHERE r.task_id IS NOT NULL AND c.task_id IS NULL"),
            ("wrong_current_table", "明细与文件的当前表归属不一致。", "SELECT r.task_id FROM data_rows r JOIN file_current_data c ON c.task_id=r.task_id WHERE c.table_id IS NULL OR r.table_id!=c.table_id OR c.has_data=0"),
            ("orphan_file_revision", "修订历史所属的文件不存在。", "SELECT h.task_id FROM file_revisions h LEFT JOIN tasks t ON t.id=h.task_id WHERE t.id IS NULL"),
            ("missing_file_revision", "文件缺少修订历史基线。", "SELECT c.task_id FROM file_current_data c WHERE NOT EXISTS(SELECT 1 FROM file_revisions h WHERE h.task_id=c.task_id)"),
            ("missing_current_details", "非空文件缺少当前数据行。", "SELECT c.task_id FROM file_current_data c WHERE c.has_data=1 AND NOT EXISTS(SELECT 1 FROM data_rows r WHERE r.task_id=c.task_id)"),
            ("nonempty_cleared_header", "已清空文件仍保留当前文件字段。", "SELECT c.task_id FROM file_current_data c WHERE c.has_data=0 AND c.header_json!='{}'"),
        )
        for code, message, query in checks:
            for row in connection.execute(query):
                issue(code, message, file_id=row[0])
        for row in connection.execute("SELECT r.task_id FROM data_rows r JOIN file_current_data c ON c.task_id=r.task_id, json_each(r.row_json) d, json_each(c.header_json) h WHERE d.key=h.key GROUP BY r.task_id"):
            issue("duplicate_current_header", "文件字段仍重复保存在明细中，不是单一事实源。", file_id=row[0])
        row_sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='data_rows'").fetchone()[0]
        if "AUTOINCREMENT" not in row_sql.upper():
            issue("reusable_detail_identity", "明细编号仍可能被删除后的新行复用。")
        report["ready"] = not issues
        if report["ready"]:
            issues.append({"code": "already_migrated", "severity": "info",
                           "message": "已使用完整的当前数据结构，无须重复迁移旧快照。"})
        return report

    matched = 0
    empty = 0
    for extraction in connection.execute(
        "SELECT e.*, t.filename FROM extractions e JOIN tasks t ON t.id=e.task_id ORDER BY e.task_id"
    ):
        task_id = extraction["task_id"]
        identity = {"file_id": task_id, "filename": extraction["filename"]}
        review = connection.execute(
            "SELECT result_json FROM review_revisions WHERE task_id=? ORDER BY version DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        raw = review[0] if review else extraction["result_json"]
        try:
            header, items = split_legacy_result(raw, custom=extraction["document_kind"] == "custom")
            rows = connection.execute(
                "SELECT id, table_id, item_index, row_json FROM data_rows WHERE task_id=? ORDER BY item_index, id",
                (task_id,),
            ).fetchall()
            actual = [_business_values(_object(row["row_json"])) for row in rows]
            expected = [_business_values({**header, **item}) for item in items or [{}]]
        except (ValueError, TypeError) as error:
            issue("invalid_legacy_json", "旧数据结构无效，需单独处理后迁移。", **identity,
                  error_type=type(error).__name__)
            continue
        matches = _same_json(actual, expected) and [row["item_index"] for row in rows] == list(range(1, len(rows) + 1))
        overlap = set(header) & {key for item in items for key in item}
        if overlap:
            issues.append({"code": "header_item_key_collision", "severity": "info" if matches else "blocker",
                           "message": "旧文件结构的文件字段与明细同名；平面值一致时分别保留两者并规范明细标识，存在不同值时仍须核对。",
                           **identity, "field_keys": sorted(overlap)})
        if len({row["table_id"] for row in rows}) > 1:
            issue("file_in_multiple_tables", "同一文件的明细分布在不同原始表，需要先明确归属。", **identity)
        inconsistent = [key for key in header if not (matches and key in overlap) and len({
            json.dumps(value.get(key), sort_keys=True, ensure_ascii=False) for value in actual
        }) > 1]
        if inconsistent:
            issue("inconsistent_header", "同一文件的多条明细保存了不同文件字段，不能猜测哪一个正确。",
                  **identity, field_keys=inconsistent)
        if not rows:
            empty += 1
            # Old deletion could discard its own revisions. There is no safe
            # automatic way to distinguish intentional clearing from lost writes.
            issue("missing_current_rows", "文件有提取历史，但仓库没有当前数据。需确认保持为空还是采用文件结果。",
                  **identity, file_detail_count=len(items), warehouse_row_count=0)
        elif not matches:
            issue("different_current_data", "文件详情与仓库数据不一致。升级不会自动覆盖任意一边。",
                  **identity, file_detail_count=len(items), warehouse_row_count=len(rows))
        else:
            matched += 1
        confirmation = connection.execute(
            "SELECT result_json FROM confirmed_documents WHERE task_id=?", (task_id,)
        ).fetchone()
        if confirmation:
            try:
                confirmed_header, confirmed_items = split_legacy_result(
                    confirmation[0], custom=extraction["document_kind"] == "custom",
                )
                confirmed = [_business_values({**confirmed_header, **item}) for item in confirmed_items or [{}]]
                if not _same_json(confirmed, expected) and not _same_json(confirmed, actual):
                    issue("third_current_snapshot", "已确认结果与文件详情、仓库均不一致，存在第三份不同的数据。", **identity)
            except (TypeError, ValueError):
                issue("invalid_confirmation", "旧确认记录结构无效，需要单独检查。", **identity)

    for row in connection.execute(
        "SELECT r.id, r.task_id FROM data_rows r LEFT JOIN tasks t ON t.id=r.task_id "
        "WHERE r.task_id IS NOT NULL AND t.id IS NULL ORDER BY r.id"
    ):
        issue("orphan_file_row", "数据行关联的文件不存在。", row_id=row[0], file_id=row[1])
    for row in connection.execute(
        "SELECT r.id, r.table_id FROM data_rows r LEFT JOIN data_tables t ON t.id=r.table_id "
        "WHERE t.id IS NULL ORDER BY r.id"
    ):
        issue("orphan_table_row", "数据行所属的数据表不存在。", row_id=row[0], table_id=row[1])

    counts.update(matching_files=matched, files_without_rows=empty)
    counts["issues_by_code"] = dict(Counter(entry["code"] for entry in issues))
    report["ready"] = not any(entry["severity"] == "blocker" for entry in issues)
    return report


def audit_database(path: Path) -> dict[str, Any]:
    """Use SQLite read-only mode (including WAL), never create a missing DB."""
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("请指定已有的 SQLite 数据库文件。")
    with closing(sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return audit_connection(connection)
