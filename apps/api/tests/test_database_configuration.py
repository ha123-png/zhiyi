from pathlib import Path

from document_pipeline_api.db import build_engine


def test_file_sqlite_enables_wal_foreign_keys_and_busy_timeout(
    tmp_path: Path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'configured.db'}")
    try:
        with engine.connect() as connection:
            journal_mode = connection.exec_driver_sql(
                "PRAGMA journal_mode"
            ).scalar_one()
            foreign_keys = connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar_one()
            busy_timeout = connection.exec_driver_sql(
                "PRAGMA busy_timeout"
            ).scalar_one()
            synchronous = connection.exec_driver_sql(
                "PRAGMA synchronous"
            ).scalar_one()
    finally:
        engine.dispose()

    assert journal_mode == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5000
    assert synchronous == 1
