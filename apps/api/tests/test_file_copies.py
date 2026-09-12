from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import os
from pathlib import Path

import pytest

from document_pipeline_api.services.file_copies import (
    CopyExportError,
    publish_original_copy,
    suggest_safe_stem,
    validate_copy_name,
)


def publish(source: Path, directory: Path, name: str = "整理后的文件.txt"):
    content = source.read_bytes()
    return publish_original_copy(
        source, directory, name,
        expected_sha256=hashlib.sha256(content).hexdigest(), expected_size=len(content),
    )


def test_complete_copy_preserves_source_and_does_not_follow_later_external_changes(tmp_path):
    source = tmp_path / "original.txt"
    content = "完整原件含未送入模型的正文\n".encode() * 100000
    source.write_bytes(content)
    destination = tmp_path / "external"
    destination.mkdir()
    result = publish(source, destination)
    assert result.path.read_bytes() == source.read_bytes() == content
    result.path.write_bytes(b"user edited external copy")
    assert source.read_bytes() == content
    assert not list(destination.glob(".zhiyi-copy-*"))


def test_concurrent_same_name_publishes_once_and_never_overwrites(tmp_path):
    source = tmp_path / "original.txt"
    source.write_bytes(b"original")
    destination = tmp_path / "external"
    destination.mkdir()

    def attempt(_):
        try:
            return publish(source, destination)
        except CopyExportError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(result == "name_conflict" for result in results) == 3
    assert (destination / "整理后的文件.txt").read_bytes() == b"original"
    assert not list(destination.glob(".zhiyi-copy-*"))


def test_existing_user_file_is_preserved_even_if_bytes_match(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"same")
    target = tmp_path / "user.txt"
    target.write_bytes(b"same")
    with pytest.raises(CopyExportError) as caught:
        publish(source, tmp_path, target.name)
    assert caught.value.code == "name_conflict"
    assert target.read_bytes() == source.read_bytes() == b"same"


def test_missing_destination_does_not_recreate_disconnected_directory(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    missing = tmp_path / "missing"
    with pytest.raises(CopyExportError) as caught:
        publish(source, missing)
    assert caught.value.code == "destination_unavailable"
    assert not missing.exists()
    assert source.read_bytes() == b"original"


def test_changed_original_cannot_publish_a_misleading_copy(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"different")
    with pytest.raises(CopyExportError) as caught:
        publish_original_copy(source, tmp_path, "target.txt", expected_size=9, expected_sha256="0" * 64)
    assert caught.value.code == "original_changed"
    assert not (tmp_path / "target.txt").exists()
    assert not list(tmp_path.glob(".zhiyi-copy-*"))
    assert source.read_bytes() == b"different"


def test_permission_failure_never_removes_source(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("document_pipeline_api.services.file_copies.tempfile.mkstemp", denied)
    with pytest.raises(CopyExportError) as caught:
        publish(source, tmp_path)
    assert caught.value.code == "permission_denied"
    assert source.read_bytes() == b"original"


def test_destination_created_during_copy_is_never_overwritten(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    target = tmp_path / "整理后的文件.txt"
    original_fsync = os.fsync

    def other_program_writes(descriptor):
        original_fsync(descriptor)
        target.write_bytes(b"belongs to another program")

    monkeypatch.setattr("document_pipeline_api.services.file_copies.os.fsync", other_program_writes)
    with pytest.raises(CopyExportError) as caught:
        publish(source, tmp_path)
    assert caught.value.code == "name_conflict"
    assert target.read_bytes() == b"belongs to another program"
    assert not list(tmp_path.glob(".zhiyi-copy-*"))


def test_flush_failure_leaves_no_published_partial_copy(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")

    def disk_full(descriptor):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("document_pipeline_api.services.file_copies.os.fsync", disk_full)
    with pytest.raises(CopyExportError) as caught:
        publish(source, tmp_path)
    assert caught.value.code == "copy_failed"
    assert not (tmp_path / "整理后的文件.txt").exists()
    assert not list(tmp_path.glob(".zhiyi-copy-*"))
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("name", ["", "..", "../x.txt", "C:\\x.txt", "NUL.txt", "COM¹.pdf", "name.", " name", "a:b", "😀" * 91])
def test_rejects_windows_unsafe_names(name):
    with pytest.raises(CopyExportError):
        validate_copy_name(name)


def test_suggestion_is_bounded_and_keeps_meaningful_unicode():
    assert suggest_safe_stem("数学：错题/复习") == "数学：错题_复习"
    assert suggest_safe_stem("CON") == "_CON"
    assert len(suggest_safe_stem("😀" * 200).encode("utf-16-le")) <= 240
