"""Candidate evidence must detect source drift and bind non-EXE runtime files."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "release_evidence", Path(__file__).resolve().parents[3] / "scripts/release_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


@pytest.fixture
def repository(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args])
    git("init", "-q")
    (tmp_path / "app.py").write_text("original")
    (tmp_path / ".gitignore").write_text(".local/\n")
    git("add", ".")
    git("-c", "user.name=Candidate Test", "-c", "user.email=candidate@example.invalid",
        "commit", "-qm", "Synthetic baseline")
    return tmp_path


@pytest.mark.parametrize("change", ["changed", "removed", "added"])
def test_source_changes_invalidate_candidate(repository, change):
    saved = evidence.source_snapshot(repository)
    evidence.verify_source(repository, saved)
    if change == "changed":
        (repository / "app.py").write_text("modified")
    elif change == "removed":
        (repository / "app.py").unlink()
    else:
        (repository / "new.py").write_text("untracked source")
    with pytest.raises(ValueError, match="changed"):
        evidence.verify_source(repository, saved)


def test_private_build_outputs_do_not_change_source_identity(repository):
    saved = evidence.source_snapshot(repository)
    (repository / ".local").mkdir()
    (repository / ".local/result.json").write_text("synthetic evidence")
    evidence.verify_source(repository, saved)


def test_payload_binds_libraries_and_web_not_only_executable(tmp_path):
    (tmp_path / "Zhiyi.exe").write_bytes(b"synthetic executable")
    (tmp_path / "_internal").mkdir()
    library = tmp_path / "_internal/library.dll"
    library.write_bytes(b"synthetic library v1")
    first = evidence.payload_snapshot(tmp_path)
    assert first["file_count"] == 2
    library.write_bytes(b"synthetic library v2")
    second = evidence.payload_snapshot(tmp_path)
    assert first != second
    assert next(row for row in first["files"] if row["path"] == "Zhiyi.exe") == next(
        row for row in second["files"] if row["path"] == "Zhiyi.exe")
