import sqlite3
from contextlib import closing
from pathlib import Path

from document_pipeline_api.launcher import purge_local_data


def test_purge_local_data_removes_product_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "Zhiyi"
    data_dir.mkdir()
    database = data_dir / "document-pipeline.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE model_profile_versions (secret_ref TEXT)")
        connection.execute("CREATE TABLE tasks (model_secret_ref TEXT)")
    (data_dir / "user-state.json").write_text("{}", encoding="utf-8")

    purge_local_data(data_dir)

    assert not data_dir.exists()
