from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from document_pipeline_api.runtime_paths import api_resource_dir


EXPECTED_SCHEMA = {
    "tasks": {
        "id",
        "filename",
        "content_type",
        "size_bytes",
        "page_count",
        "sha256",
        "storage_path",
        "template_mode",
        "template_id",
        "template_version",
        "candidate_templates_json",
        "model_config_version",
        "model_profile_id",
        "model_profile_version",
        "model_provider",
        "model_base_url",
        "model_name",
        "model_reasoning_effort",
        "model_timeout_seconds",
        "model_context_length",
        "model_temperature",
        "model_secret_ref",
        "status",
        "attempt_count",
        "lease_token",
        "lease_expires_at",
        "failure_code",
        "failure_message",
        "duplicate_of_task_id",
        "target_table_id",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    },
    "extractions": {
        "task_id",
        "document_kind",
        "template_id",
        "template_version",
        "model_name",
        "prompt_version",
        "rule_engine_version",
        "elapsed_seconds",
        "result_json",
        "validation_json",
        "rule_engine_version",
        "evidence_json",
        "created_at",
    },
    "review_revisions": {
        "id",
        "task_id",
        "version",
        "result_json",
        "validation_json",
        "changes_json",
        "rule_engine_version",
        "editor",
        "created_at",
    },
    "data_tables": {
        "id",
        "name",
        "template_key",
        "template_version",
        "document_kind",
        "columns_json",
        "created_at",
    },
    "confirmed_documents": {
        "task_id",
        "table_id",
        "review_version",
        "result_json",
        "confirmed_at",
    },
    "data_rows": {
        "id",
        "table_id",
        "task_id",
        "item_index",
        "row_json",
        "row_version",
        "created_at",
        "updated_at",
    },
    "data_row_revisions": {
        "id",
        "row_id",
        "table_id",
        "task_id",
        "version",
        "operation",
        "before_json",
        "after_json",
        "editor",
        "created_at",
    },
    "data_views": {
        "id",
        "table_id",
        "name",
        "field_key",
        "field_value_json",
        "created_at",
        "updated_at",
    },
    "templates": {
        "id",
        "builtin_key",
        "is_system",
        "is_active",
        "current_version",
        "source_template_id",
        "in_smart_pool",
        "created_at",
        "updated_at",
    },
    "template_versions": {
        "id",
        "template_id",
        "version",
        "name",
        "description",
        "extra_instructions",
        "fields_json",
        "validation_rules_json",
        "deterministic_rules_json",
        "output_mapping_json",
        "created_at",
    },
    "system_settings": {
        "key",
        "value",
        "updated_at",
    },
    "model_profiles": {
        "id",
        "current_version",
        "is_archived",
        "created_at",
        "updated_at",
    },
    "model_profile_versions": {
        "id",
        "profile_id",
        "version",
        "name",
        "provider",
        "base_url",
        "model_name",
        "reasoning_effort",
        "timeout_seconds",
        "context_length",
        "temperature",
        "secret_ref",
        "is_remote",
        "remote_data_acknowledged",
        "multimodal",
        "created_at",
    },
    "model_runtime_state": {
        "id",
        "active_profile_id",
        "active_profile_version",
        "updated_at",
    },
}
SCHEMA_REVISIONS = {
    "0001_initial": {"tasks", "extractions"},
    "0002_review_revisions": {
        "tasks",
        "extractions",
        "review_revisions",
    },
    "0003_confirmed_data_tables": {
        "tasks",
        "extractions",
        "review_revisions",
        "data_tables",
        "confirmed_documents",
        "data_rows",
    },
    "0004_template_versions": {
        "tasks",
        "extractions",
        "review_revisions",
        "data_tables",
        "confirmed_documents",
        "data_rows",
        "templates",
        "template_versions",
    },
    "0005_task_template_snapshots": {
        "tasks",
        "extractions",
        "review_revisions",
        "data_tables",
        "confirmed_documents",
        "data_rows",
        "templates",
        "template_versions",
    },
    "0006_task_processing_leases": {
        "tasks",
        "extractions",
        "review_revisions",
        "data_tables",
        "confirmed_documents",
        "data_rows",
        "templates",
        "template_versions",
    },
    "0007_data_row_versions_and_views": {
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
    },
    "0008_task_page_counts": {
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
    },
    "0009_extraction_evidence": {
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
    },
    "0010_template_deterministic_rules": {
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
    },
    "0011_template_archival": {
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
    },
    "0012_rule_engine_versions": {
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
    },
    "0013_task_model_snapshots": {
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
    },
    "0014_model_profiles": {
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
    },
    "0015_manual_table_rows": {
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
    },
    "0016_model_profile_context": {
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
    },
}
# 0017-0020 是当前用户设置、指定目标表、智能匹配池与计时字段；
# 0021 只新增索引，表/列结构与 0020 相同。
# 接管无 alembic_version 的数据库时必须能精确识别这些结构；否则当前结构会被
# 错盖章为 0016，随后重复 CREATE TABLE/ADD COLUMN 并导致恢复失败。
SCHEMA_REVISIONS.update(
    {
        "0017_system_settings": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
        "0018_task_target_table": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
        "0019_template_smart_pool": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
        "0020_task_started_at": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
        "0021_task_list_scale": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
        "0022_task_completed_at": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
        "0023_model_profile_multimodal": SCHEMA_REVISIONS["0016_model_profile_context"]
        | {"system_settings"},
    }
)
COLUMNS_INTRODUCED_BY_REVISION = {
    "0023_model_profile_multimodal": {
        "model_profile_versions": {"multimodal"},
    },
    "0022_task_completed_at": {
        "tasks": {"completed_at"},
    },
    "0020_task_started_at": {
        "tasks": {"started_at"},
    },
    "0019_template_smart_pool": {
        "templates": {"in_smart_pool"},
    },
    "0018_task_target_table": {
        "tasks": {"target_table_id"},
    },
    "0017_system_settings": {
        "system_settings": {"key", "value", "updated_at"},
    },
    "0016_model_profile_context": {
        "model_profile_versions": {"context_length", "temperature"},
        "tasks": {"model_context_length", "model_temperature"},
    },
    "0015_manual_table_rows": {
        "data_tables": {"columns_json"},
    },
    "0014_model_profiles": {
        "tasks": {"model_profile_id", "model_profile_version"},
    },
    "0013_task_model_snapshots": {
        "tasks": {
            "model_config_version",
            "model_provider",
            "model_base_url",
            "model_name",
            "model_reasoning_effort",
            "model_timeout_seconds",
            "model_secret_ref",
        },
    },
    "0012_rule_engine_versions": {
        "extractions": {"rule_engine_version"},
        "review_revisions": {"rule_engine_version"},
    },
    "0011_template_archival": {
        "templates": {"is_active"},
    },
    "0005_task_template_snapshots": {
        "tasks": {
            "template_id",
            "template_version",
            "candidate_templates_json",
        },
        "extractions": {
            "template_id",
            "template_version",
        },
    },
    "0006_task_processing_leases": {
        "tasks": {
            "attempt_count",
            "lease_token",
            "lease_expires_at",
            "failure_code",
            "failure_message",
        },
    },
    "0007_data_row_versions_and_views": {
        "data_rows": {
            "row_version",
            "updated_at",
        },
        "data_row_revisions": {
            "id",
            "row_id",
            "table_id",
            "task_id",
            "version",
            "operation",
            "before_json",
            "after_json",
            "editor",
            "created_at",
        },
        "data_views": {
            "id",
            "table_id",
            "name",
            "field_key",
            "field_value_json",
            "created_at",
            "updated_at",
        },
    },
    "0008_task_page_counts": {
        "tasks": {
            "page_count",
        },
    },
    "0009_extraction_evidence": {
        "extractions": {
            "evidence_json",
        },
    },
    "0010_template_deterministic_rules": {
        "template_versions": {
            "deterministic_rules_json",
        },
    },
}


def upgrade_database(engine: Engine) -> None:
    config = _alembic_config()
    try:
        backup_path = _backup_before_upgrade(engine, config)
    except Exception as error:
        raise DatabaseBackupError() from error
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            inspector = inspect(connection)
            existing_tables = set(inspector.get_table_names())
            application_tables = existing_tables & EXPECTED_SCHEMA.keys()

            if "alembic_version" not in existing_tables and application_tables:
                actual_columns = {
                    table: {
                        column["name"] for column in inspector.get_columns(table)
                    }
                    for table in application_tables
                }
                matching_revision = next(
                    (
                        revision
                        for revision, tables in reversed(SCHEMA_REVISIONS.items())
                        if application_tables == tables
                        and all(
                            _expected_columns_at_revision(table, revision)
                            <= actual_columns[table]
                            for table in application_tables
                        )
                    ),
                    None,
                )
                if matching_revision is None:
                    raise RuntimeError("数据库结构不完整，不能自动接管迁移。")
                command.stamp(config, matching_revision)
                command.upgrade(config, "head")
                return

            command.upgrade(config, "head")
    except Exception as error:
        if backup_path is None:
            raise
        raise DatabaseMigrationError(backup_path) from error


def wait_for_database_head(engine: Engine, timeout: float = 30.0) -> None:
    config = _alembic_config()
    expected = ScriptDirectory.from_config(config).get_current_head()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:
                current = MigrationContext.configure(connection).get_current_revision()
            if current == expected:
                return
        except OperationalError:
            pass
        time.sleep(0.1)
    raise RuntimeError("数据库升级尚未完成，任务处理器未启动。")


class DatabaseMigrationError(RuntimeError):
    def __init__(self, backup_path: Path) -> None:
        self.backup_path = backup_path
        super().__init__(
            "数据库升级失败，原数据未被自动覆盖。"
            f"升级前备份保存在：{backup_path}"
        )


class DatabaseBackupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "无法创建数据库升级前备份，升级已停止。"
            "请确认备份目录可写且磁盘空间充足后重试。"
        )


def _backup_before_upgrade(engine: Engine, config: Config) -> Path | None:
    database = engine.url.database
    if engine.dialect.name != "sqlite" or not database or database == ":memory:":
        return None
    database_path = Path(database).resolve()
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None
    with engine.connect() as connection:
        existing_tables = set(inspect(connection).get_table_names())
        if not (existing_tables & EXPECTED_SCHEMA.keys()):
            return None
        current_revision = MigrationContext.configure(connection).get_current_revision()
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    if current_revision == head_revision:
        return None

    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    revision_label = current_revision or "unversioned"
    backup_path = backup_dir / (
        f"{database_path.stem}.pre-{revision_label}.{timestamp}.db"
    )
    raw_connection = engine.raw_connection()
    try:
        source = raw_connection.driver_connection
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError("升级前数据库备份完整性检查失败。")
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        raw_connection.close()
    return backup_path


def _expected_columns_at_revision(table: str, revision: str) -> set[str]:
    expected = set(EXPECTED_SCHEMA[table])
    revisions = list(SCHEMA_REVISIONS)
    revision_index = revisions.index(revision)
    for later_revision in revisions[revision_index + 1 :]:
        expected -= COLUMNS_INTRODUCED_BY_REVISION.get(later_revision, {}).get(
            table,
            set(),
        )
    return expected


def _alembic_config() -> Config:
    api_root = api_resource_dir()
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "migrations"))
    return config
