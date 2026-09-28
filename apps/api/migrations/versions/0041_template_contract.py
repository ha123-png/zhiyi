"""Starter templates are ordinary rules; versions no longer split target tables."""

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0041_template_contract"
down_revision = "0040_file_current_data"
branch_labels = None
depends_on = None


ALIASES = {"amount": "item_amount", "tax_amount": "item_tax_amount", "remarks": "item_remarks"}


def _normalize_custom_current(connection, template):
    from document_pipeline_api.services.legacy_fields import normalize_fields, normalize_template_payload
    version = dict(connection.execute(sa.text(
        'SELECT * FROM template_versions WHERE template_id=:id AND version=:version'),
        {'id': template['id'], 'version': template['current_version']}).mappings().one())
    _, aliases = normalize_fields(json.loads(version['fields_json']))
    if not aliases:
        return
    version.pop('id')
    payload = normalize_template_payload({
        key: json.loads(version[column]) for key, column in (
            ('fields', 'fields_json'), ('deterministic_rules', 'deterministic_rules_json'),
            ('output_mapping', 'output_mapping_json'), ('behavior', 'behavior_json'))})
    for key, value in payload.items():
        version[key + '_json'] = json.dumps(value, ensure_ascii=False)
    version['version'] = connection.execute(sa.text(
        'SELECT max(version)+1 FROM template_versions WHERE template_id=:id'), {'id': template['id']}).scalar_one()
    version['created_at'] = datetime.now(timezone.utc).replace(tzinfo=None)
    connection.execute(sa.text('INSERT INTO template_versions (' + ','.join(version) + ') VALUES (' +
                               ','.join(':' + key for key in version) + ')'), version)
    connection.execute(sa.text('UPDATE templates SET current_version=:version, updated_at=:now WHERE id=:id'),
                       {'id': template['id'], 'version': version['version'], 'now': version['created_at']})


def _references(value):
    if isinstance(value, dict):
        return {key: _references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_references(item) for item in value]
    if isinstance(value, str):
        for prefix in ("items[].", "item."):
            if value.startswith(prefix) and value[len(prefix) :] in ALIASES:
                return prefix + ALIASES[value[len(prefix) :]]
    return value


def _ordinary_rules(kind, fields, existing):
    """Make formerly hardcoded starter checks visible, editable template rules."""
    paths = {
        ("items[]" if field.get("section") == "item" else "header") + "." + field["key"]
        for field in fields
    }
    candidates = [
        {"kind": "required", "field": "header." + key, "severity": "warning"}
        for key in ("seller_name", "buyer_name", "document_number", "document_date", "total_amount")
    ]
    candidates.append(
        {
            "kind": "equation",
            "field": "items[].item_amount",
            "left": {
                "op": "multiply",
                "left": {"op": "field", "path": "items[].quantity"},
                "right": {"op": "field", "path": "items[].unit_price"},
            },
            "right": {"op": "field", "path": "items[].item_amount"},
        }
    )
    total = "header.amount_before_tax" if kind == "invoice" else "header.total_amount"
    candidates.append(
        {
            "kind": "equation",
            "field": total,
            "left": {"op": "sum", "path": "items[].item_amount"},
            "right": {"op": "field", "path": total},
        }
    )
    if kind == "invoice":
        candidates.append(
            {
                "kind": "equation",
                "field": "header.total_amount",
                "left": {
                    "op": "add",
                    "left": {"op": "field", "path": "header.amount_before_tax"},
                    "right": {"op": "field", "path": "header.tax_amount"},
                },
                "right": {"op": "field", "path": "header.total_amount"},
            }
        )

    def references(value):
        if not isinstance(value, dict):
            return set()
        found = {value[key] for key in ("path", "field") if key in value}
        for child in value.values():
            found |= references(child)
        return found

    rules = list(existing)
    identities = {(rule["kind"], rule["field"]) for rule in rules}
    for rule in candidates:
        if references(rule) <= paths and (rule["kind"], rule["field"]) not in identities:
            rules.append(rule)
    return rules


def upgrade():
    connection = op.get_bind()
    raw = connection.connection.driver_connection
    if not raw.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    op.add_column(
        "templates", sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.add_column(
        "template_versions",
        sa.Column("engine_kind", sa.String(16), nullable=False, server_default="template"),
    )
    op.create_table(
        "template_table_bindings",
        sa.Column("template_id", sa.String(36), sa.ForeignKey("templates.id"), primary_key=True),
        sa.Column(
            "table_id", sa.String(36), sa.ForeignKey("data_tables.id"), nullable=False, unique=True
        ),
    )
    templates = connection.execute(sa.text("SELECT * FROM templates")).mappings().all()
    for template in templates:
        builtin = template["builtin_key"] in {"invoice", "delivery"}
        if not builtin:
            _normalize_custom_current(connection, template)
        if builtin:
            # Old shortcut modes did not store a template ID at upload time.
            # Pin those jobs before converting the starter to a new rule version.
            connection.execute(
                sa.text(
                    "UPDATE tasks SET template_id=:id, template_version=:version "
                    "WHERE template_id IS NULL AND template_mode=:mode"
                ),
                {
                    "id": template["id"],
                    "version": template["current_version"],
                    "mode": template["builtin_key"],
                },
            )
            connection.execute(
                sa.text("UPDATE template_versions SET engine_kind='legacy' WHERE template_id=:id"),
                {"id": template["id"]},
            )
            current = dict(
                connection.execute(
                    sa.text(
                        "SELECT * FROM template_versions WHERE template_id=:id AND version=:version"
                    ),
                    {"id": template["id"], "version": template["current_version"]},
                )
                .mappings()
                .one()
            )
            current.pop("id")
            fields = json.loads(current["fields_json"])
            for field in fields:
                if field.get("section") == "item":
                    field["key"] = ALIASES.get(field["key"], field["key"])
            current["fields_json"] = json.dumps(fields, ensure_ascii=False)
            for key in ("behavior_json", "deterministic_rules_json"):
                current[key] = json.dumps(_references(json.loads(current[key])), ensure_ascii=False)
            current["deterministic_rules_json"] = json.dumps(
                _ordinary_rules(
                    template["builtin_key"], fields, json.loads(current["deterministic_rules_json"])
                ),
                ensure_ascii=False,
            )
            mapping = json.loads(current["output_mapping_json"])
            for field in fields:
                old_key = next((old for old, new in ALIASES.items() if new == field["key"]), None)
                if field.get("section") == "item" and old_key in mapping:
                    mapping[field["key"]] = mapping[old_key]
            current["output_mapping_json"] = json.dumps(mapping, ensure_ascii=False)
            current["version"] = connection.execute(
                sa.text("SELECT max(version)+1 FROM template_versions WHERE template_id=:id"),
                {"id": template["id"]},
            ).scalar_one()
            current["engine_kind"] = "template"
            current["created_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
            columns = ", ".join(current)
            placeholders = ", ".join(":" + key for key in current)
            connection.execute(
                sa.text(f"INSERT INTO template_versions ({columns}) VALUES ({placeholders})"),
                current,
            )
            connection.execute(
                sa.text(
                    "UPDATE templates SET is_system=0, current_version=:version, updated_at=:now WHERE id=:id"
                ),
                {"version": current["version"], "now": current["created_at"], "id": template["id"]},
            )
        # Preserve every old table and its values. Choose the former current
        # version's table as the future destination, without merging old tables.
        legacy_key = template["builtin_key"] if builtin else template["id"]
        tables = (
            connection.execute(
                sa.text(
                    "SELECT * FROM data_tables WHERE template_key=:key ORDER BY created_at, id"
                ),
                {"key": legacy_key},
            )
            .mappings()
            .all()
        )
        for table in tables:
            if not table["columns_json"]:
                version = template["current_version"] if builtin else int(table["template_version"])
                source = (
                    connection.execute(
                        sa.text(
                            "SELECT fields_json, output_mapping_json, behavior_json FROM template_versions WHERE template_id=:id AND version=:version"
                        ),
                        {"id": template["id"], "version": version},
                    )
                    .mappings()
                    .one()
                )
                mapping = json.loads(source["output_mapping_json"])
                fields = json.loads(source["fields_json"])
                columns = [
                    {
                        "key": ALIASES.get(field["key"], field["key"])
                        if builtin and field.get("section") == "item"
                        else field["key"],
                        "label": mapping.get(field["key"], field["label"]),
                        "section": field.get("section", "header"),
                        "value_type": field.get("value_type", "text"),
                    }
                    for field in fields
                ]
                if not builtin:
                    from document_pipeline_api.services.legacy_fields import normalize_fields
                    columns, _ = normalize_fields(columns)
                connection.execute(
                    sa.text(
                        "UPDATE data_tables SET columns_json=:columns, presentation_json=:presentation WHERE id=:id"
                    ),
                    {
                        "columns": json.dumps(columns, ensure_ascii=False),
                        "presentation": json.dumps(
                            json.loads(source["behavior_json"]).get("presentation", {})
                        ),
                        "id": table["id"],
                    },
                )
            if builtin:
                connection.execute(
                    sa.text("UPDATE data_tables SET template_key=:key WHERE id=:id"),
                    {"key": template["id"], "id": table["id"]},
                )
        if tables:
            preferred = next(
                (
                    table
                    for table in tables
                    if table["template_version"] == str(template["current_version"])
                ),
                tables[-1],
            )
            connection.execute(
                sa.text(
                    "INSERT INTO template_table_bindings (template_id,table_id) VALUES (:template,:table)"
                ),
                {"template": template["id"], "table": preferred["id"]},
            )
    if templates:
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO system_settings (key,value,updated_at) VALUES ('starter_templates_initialized','true',CURRENT_TIMESTAMP)"
            )
        )


def downgrade():
    raise RuntimeError("请使用升级前完整备份恢复旧版本；不能把用户编辑过的模板退回只读内置模板。")
