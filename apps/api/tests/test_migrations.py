import json
import sqlite3

import pytest
from alembic import command
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    inspect,
    select,
)
from sqlalchemy.orm import Session

from document_pipeline_api.db import build_engine
from document_pipeline_api.migrations import (
    DatabaseBackupError,
    DatabaseMigrationError,
    _alembic_config,
    upgrade_database,
)
from document_pipeline_api.models import TaskRecord


def test_upgrade_database_creates_schema_from_empty_database(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    upgrade_database(engine)

    assert {
        "alembic_version",
        "tasks",
        "extractions",
        "review_revisions",
        "data_tables",
        "confirmed_documents",
        "data_rows",
        "data_row_revisions",
        "data_views",
        "templates",
        "template_versions",
        "model_profiles",
        "model_profile_versions",
        "model_runtime_state",
    } <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(
            select(_version_table(engine))
        ).scalar_one() == "0039_original_archive"
    assert "rule_engine_version" in {
        column["name"] for column in inspect(engine).get_columns("extractions")
    }
    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert {
        "model_config_version",
        "model_profile_id",
        "model_profile_version",
        "model_provider",
        "model_base_url",
        "model_name",
        "model_reasoning_effort",
        "model_timeout_seconds",
        "model_secret_ref",
    } <= task_columns
    assert "target_table_id" in task_columns
    assert "started_at" in task_columns
    assert "completed_at" in task_columns
    assert "rule_engine_version" in {
        column["name"] for column in inspect(engine).get_columns("review_revisions")
    }


def test_upgrade_database_stamps_existing_schema_without_losing_data(
    tmp_path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    _create_legacy_initial_schema(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO tasks (
                id, filename, content_type, size_bytes, sha256, storage_path,
                template_mode, status, created_at, updated_at
            ) VALUES (
                'existing-task', 'invoice.png', 'image/png', 1, 'digest',
                'invoice.png', 'invoice', 'queued', CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            """
        )

    upgrade_database(engine)

    with Session(engine) as session:
        task = session.get(TaskRecord, "existing-task")
        assert task is not None
        assert task.page_count == 1
    assert {
        "alembic_version",
        "review_revisions",
        "data_tables",
        "confirmed_documents",
        "data_rows",
        "data_row_revisions",
        "data_views",
    } <= set(
        inspect(engine).get_table_names()
    )


def test_unversioned_0005_database_is_not_mistaken_for_0006(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'unversioned-0005.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005_task_template_snapshots")
        connection.exec_driver_sql("DROP TABLE alembic_version")

    upgrade_database(engine)

    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert {
        "attempt_count",
        "lease_token",
        "lease_expires_at",
        "failure_code",
        "failure_message",
    } <= task_columns
    with engine.connect() as connection:
        assert connection.execute(
            select(_version_table(engine))
        ).scalar_one() == "0039_original_archive"


def test_unversioned_0006_database_is_upgraded_to_row_versions(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'unversioned-0006.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006_task_processing_leases")
        connection.exec_driver_sql("DROP TABLE alembic_version")

    upgrade_database(engine)

    row_columns = {
        column["name"] for column in inspect(engine).get_columns("data_rows")
    }
    assert {"row_version", "updated_at"} <= row_columns
    assert {"data_row_revisions", "data_views"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(
            select(_version_table(engine))
        ).scalar_one() == "0039_original_archive"


def test_unversioned_current_database_is_adopted_without_replaying_migrations(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'unversioned-current.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.exec_driver_sql(
            "INSERT INTO system_settings (key, value) VALUES ('image_convert', 'true')"
        )
        connection.exec_driver_sql("DROP TABLE alembic_version")

    upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.execute(select(_version_table(engine))).scalar_one() == (
            "0039_original_archive"
        )
        assert connection.exec_driver_sql(
            "SELECT value FROM system_settings WHERE key = 'image_convert'"
        ).scalar_one() == "true"


def test_0010_backfills_rules_for_existing_builtin_template_versions(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'builtin-rules.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0009_extraction_evidence")
        connection.exec_driver_sql(
            """
            INSERT INTO templates (
                id, builtin_key, is_system, source_template_id, current_version,
                created_at, updated_at
            ) VALUES (
                'builtin-invoice', 'invoice', 1, NULL, 1,
                '2026-08-01 00:00:00', '2026-08-01 00:00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO template_versions (
                template_id, version, name, description, extra_instructions,
                fields_json, validation_rules_json, output_mapping_json, created_at
            ) VALUES (
                'builtin-invoice', 1, '发票', '', '', '[]', '[]', '{}',
                '2026-08-01 00:00:00'
            )
            """
        )

    upgrade_database(engine)

    with engine.connect() as connection:
        stored = connection.exec_driver_sql(
            "SELECT deterministic_rules_json FROM template_versions "
            "WHERE template_id = 'builtin-invoice'"
        ).scalar_one()
    rules = json.loads(stored)
    assert any(rule["kind"] == "required" for rule in rules)
    assert any(rule["kind"] == "equation" for rule in rules)
    # 迁移回填的是 0010 时点的规则快照（items[].item_amount），
    # 与后来更新的内置模板定义（items[].amount）不同属预期：旧版本快照保留可追溯。
    assert any(
        rule.get("kind") == "equation" and "item_amount" in json.dumps(rule, ensure_ascii=False)
        for rule in rules
    )


def test_0010_recovers_when_column_was_added_before_revision_was_recorded(
    tmp_path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'partial-0010.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0009_extraction_evidence")
        connection.exec_driver_sql(
            "ALTER TABLE template_versions "
            "ADD COLUMN deterministic_rules_json TEXT NOT NULL DEFAULT '[]'"
        )

    upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.execute(select(_version_table(engine))).scalar_one() == (
            "0039_original_archive"
        )
    assert [
        column["name"]
        for column in inspect(engine).get_columns("template_versions")
    ].count("deterministic_rules_json") == 1


def test_upgrade_creates_consistent_backup_only_when_revision_changes(tmp_path) -> None:
    database = tmp_path / "upgrade-backup.db"
    engine = build_engine(f"sqlite:///{database}")
    _seed_0010_template(engine)
    assert database.with_name(f"{database.name}-wal").exists()

    upgrade_database(engine)

    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0010_template_deterministic_rules",)
        assert connection.execute("SELECT count(*) FROM templates").fetchone() == (1,)

    upgrade_database(engine)
    assert list((tmp_path / "backups").glob("*.db")) == backups


def test_backup_failure_stops_upgrade_with_readable_error(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "backup-failure.db"
    engine = build_engine(f"sqlite:///{database}")
    _seed_0010_template(engine)

    def fail_to_open_backup(*args, **kwargs):
        raise OSError("simulated full disk")

    monkeypatch.setattr("document_pipeline_api.migrations.sqlite3.connect", fail_to_open_backup)

    with pytest.raises(DatabaseBackupError, match="升级已停止"):
        upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.execute(select(_version_table(engine))).scalar_one() == (
            "0010_template_deterministic_rules"
        )


def test_migration_failure_reports_readable_pre_upgrade_backup(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "failed-upgrade.db"
    engine = build_engine(f"sqlite:///{database}")
    _seed_0010_template(engine)

    def fail_upgrade(config, revision) -> None:
        connection = config.attributes["connection"]
        connection.exec_driver_sql("DELETE FROM template_versions")
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr("document_pipeline_api.migrations.command.upgrade", fail_upgrade)

    with pytest.raises(DatabaseMigrationError) as captured:
        upgrade_database(engine)

    backup = captured.value.backup_path
    assert backup.exists()
    assert str(backup) in str(captured.value)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT count(*) FROM template_versions"
        ).fetchone() == (1,)


def test_0014_upgrade_keeps_tasks_referenced_by_child_rows(tmp_path) -> None:
    """0014 加列前，子表（extractions 等）已有外键引用行时不得丢数据或失败。

    该场景曾因 batch_alter_table 重建父表在 PRAGMA foreign_keys=ON 下 DROP 失败；
    回归保护改为原生 ADD COLUMN 后，有业务数据的库仍能升级到 head 且数据保留。
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'fk-0014.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0013_task_model_snapshots")
        connection.exec_driver_sql(
            """
            INSERT INTO tasks (
                id, filename, content_type, size_bytes, sha256, storage_path,
                template_mode, status, model_config_version, created_at, updated_at
            ) VALUES (
                'fk-task', 'invoice.png', 'image/png', 1, 'digest',
                'invoice.png', 'invoice', 'queued', 'legacy-unversioned',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO extractions (
                task_id, document_kind, model_name, prompt_version,
                elapsed_seconds, result_json, validation_json, created_at
            ) VALUES (
                'fk-task', 'invoice', 'local-model', 'legacy',
                1.0, '{}', '{}', CURRENT_TIMESTAMP
            )
            """
        )

    upgrade_database(engine)

    with engine.connect() as connection:
        assert connection.execute(select(_version_table(engine))).scalar_one() == (
            "0039_original_archive"
        )
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM tasks"
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM extractions"
        ).scalar_one() == 1
    task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    assert {"model_profile_id", "model_profile_version"} <= task_columns


def _seed_0010_template(engine) -> None:
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0010_template_deterministic_rules")
        connection.exec_driver_sql(
            """
            INSERT INTO templates (
                id, builtin_key, is_system, source_template_id, current_version,
                created_at, updated_at
            ) VALUES (
                'custom-template', NULL, 0, NULL, 1,
                '2026-08-01 00:00:00', '2026-08-01 00:00:00'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO template_versions (
                template_id, version, name, description, extra_instructions,
                fields_json, validation_rules_json, deterministic_rules_json,
                output_mapping_json, created_at
            ) VALUES (
                'custom-template', 1, '测试模板', '', '', '[]', '[]', '[]', '{}',
                '2026-08-01 00:00:00'
            )
            """
        )


def _version_table(engine):
    return Table("alembic_version", MetaData(), autoload_with=engine).c.version_num


def _create_legacy_initial_schema(engine) -> None:
    metadata = MetaData()
    Table(
        "tasks",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("filename", String(512), nullable=False),
        Column("content_type", String(128), nullable=False),
        Column("size_bytes", Integer, nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("storage_path", String(1024), nullable=False),
        Column("template_mode", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("duplicate_of_task_id", ForeignKey("tasks.id"), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "extractions",
        metadata,
        Column("task_id", ForeignKey("tasks.id"), primary_key=True),
        Column("document_kind", String(32), nullable=False),
        Column("model_name", String(128), nullable=False),
        Column("prompt_version", String(32), nullable=False),
        Column("elapsed_seconds", Float, nullable=False),
        Column("result_json", Text, nullable=False),
        Column("validation_json", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)


def test_0031_preserves_existing_template_versions(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'previous.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0030_row_input_scope")
    from document_pipeline_api.services.templates import ensure_builtin_templates
    with Session(engine) as session:
        ensure_builtin_templates(session)
    with engine.connect() as connection:
        before = connection.exec_driver_sql("SELECT * FROM template_versions ORDER BY id").fetchall()
    upgrade_database(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT * FROM template_versions ORDER BY id").fetchall() == before
        assert connection.exec_driver_sql("SELECT count(*) FROM template_restorations").scalar_one() == 0
