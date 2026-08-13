from pathlib import Path

from document_pipeline_api import runtime_paths


def test_frozen_default_data_dir_uses_zhiyi_name(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime_paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DOCUMENT_PIPELINE_DATA_DIR", raising=False)

    assert runtime_paths.default_data_dir() == tmp_path / "Zhiyi"


def test_frozen_default_data_dir_migrates_legacy_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "DocumentPipeline"
    legacy.mkdir()
    (legacy / "preserve.db").write_bytes(b"business-data")
    monkeypatch.setattr(runtime_paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DOCUMENT_PIPELINE_DATA_DIR", raising=False)

    current = runtime_paths.default_data_dir()

    assert current == tmp_path / "Zhiyi"
    assert (current / "preserve.db").read_bytes() == b"business-data"
    assert not legacy.exists()
