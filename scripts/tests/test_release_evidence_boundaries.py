"""A tracked source path cannot pass through a link, even within the tree."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "release_evidence.py"
SPEC = importlib.util.spec_from_file_location("release_evidence_boundary_test", SCRIPT)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


@pytest.mark.parametrize("link_method", ["is_junction", "is_symlink"])
def test_tracked_source_beneath_linked_directory_is_rejected_without_creating_links(tmp_path, monkeypatch, link_method):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "example.py").write_text("print('synthetic')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init", "--quiet"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "source/example.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True, capture_output=True)
    original = getattr(Path, link_method)
    monkeypatch.setattr(Path, link_method, lambda self: self == directory or original(self))
    with pytest.raises(ValueError, match="links"):
        evidence.source_snapshot(tmp_path)


@pytest.mark.parametrize("link_location", ["leaf", "ancestor"])
def test_dangling_tracked_link_is_rejected_not_reported_as_deleted(tmp_path, monkeypatch, link_location):
    directory = tmp_path / "source"
    directory.mkdir()
    source = directory / "example.py"
    source.write_text("print('synthetic')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init", "--quiet"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "source/example.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True, capture_output=True)
    source.unlink()
    if link_location == "ancestor":
        directory.rmdir()
    # A genuinely deleted tracked path must still be recorded as missing.
    assert any(item.get("missing") for item in evidence.source_snapshot(tmp_path)["files"])
    target = source if link_location == "leaf" else directory
    method = "is_symlink" if link_location == "leaf" else "is_junction"
    original = getattr(Path, method)
    monkeypatch.setattr(Path, method, lambda self: self == target or original(self))
    with pytest.raises(ValueError, match="links"):
        evidence.source_snapshot(tmp_path)
