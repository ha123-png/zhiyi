import json
from datetime import datetime, timezone

from alembic import command
from sqlalchemy import MetaData, Table

from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import _alembic_config


def test_upgrade_preserves_legacy_rules_and_binds_existing_destination(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'templates.db'}")
    config = _alembic_config()
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0040_file_current_data")
        metadata = MetaData()
        templates = Table("templates", metadata, autoload_with=engine)
        versions = Table("template_versions", metadata, autoload_with=engine)
        tables = Table("data_tables", metadata, autoload_with=engine)
        fields = [
            {"key": "tax_amount", "label": "总税额", "section": "header", "value_type": "number"},
            {"key": "tax_amount", "label": "税额", "section": "item", "value_type": "number"},
            {"key": "amount", "label": "金额", "section": "item", "value_type": "number"},
        ]
        rules = [{"kind": "required", "field": "items[].amount", "severity": "warning"}]
        snapshot = json.dumps(fields, ensure_ascii=False)
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                templates.insert().values(
                    id="builtin-invoice",
                    builtin_key="invoice",
                    is_system=True,
                    is_active=True,
                    current_version=1,
                    in_smart_pool=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                versions.insert().values(
                    template_id="builtin-invoice",
                    version=1,
                    name="发票",
                    description="原说明",
                    extra_instructions="保留",
                    fields_json=snapshot,
                    validation_rules_json="[]",
                    deterministic_rules_json=json.dumps(rules),
                    output_mapping_json="{}",
                    behavior_json="{}",
                    created_at=now,
                )
            )
            connection.execute(
                tables.insert().values(
                    id="existing",
                    name="用户命名",
                    template_key="invoice",
                    template_version="builtin-v1",
                    document_kind="invoice",
                    created_at=now,
                )
            )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0041_template_contract")
            assert connection.exec_driver_sql(
                "SELECT is_system, current_version, in_smart_pool FROM templates"
            ).one() == (0, 2, 0)
            original = connection.exec_driver_sql(
                "SELECT fields_json, engine_kind FROM template_versions WHERE version=1"
            ).one()
            assert original == (snapshot, "legacy")
            current = connection.exec_driver_sql(
                "SELECT fields_json, deterministic_rules_json, engine_kind FROM template_versions WHERE version=2"
            ).one()
            assert [field["key"] for field in json.loads(current[0])] == [
                "tax_amount",
                "item_tax_amount",
                "item_amount",
            ]
            assert json.loads(current[1])[0]["field"] == "items[].item_amount"
            assert current[2] == "template"
            assert connection.exec_driver_sql(
                "SELECT template_id, table_id FROM template_table_bindings"
            ).one() == ("builtin-invoice", "existing")
            assert connection.exec_driver_sql(
                "SELECT name, template_key FROM data_tables"
            ).one() == ("用户命名", "builtin-invoice")
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0041_template_contract")
            assert (
                connection.exec_driver_sql("SELECT count(*) FROM template_versions").scalar_one()
                == 2
            )
    finally:
        engine.dispose()
