"""Read-only original windows; real Office files, bounded work and cancellation."""
import asyncio
import hashlib
import io
from pathlib import Path
from threading import Event
import tracemalloc
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image
import pytest

from document_pipeline_api.api.tasks import _read_preview
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.services.document_preview import (
    BLOCK_WINDOW, CELL_CHARS, TEXT_WINDOW_BYTES, docx_window, image_thumbnail,
    text_window, xlsx_window,
)
from document_pipeline_api.services.file_formats import UnsupportedTextFileError

BUDGET = 64 * 1024 * 1024


def write_docx(path: Path, body: str):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "gb18030"])
def test_text_windows_reconstruct_unicode_without_full_file_read(tmp_path, monkeypatch, encoding):
    original = ("这是多字节文字与数字 123\n" * 8000)
    path = tmp_path / "text.txt"
    path.write_bytes(original.encode(encoding))
    def forbid_read_bytes(_self):
        raise AssertionError("whole-file reading is forbidden")
    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)
    offset = 0
    pieces = []
    while True:
        result = text_window(path, offset)
        assert len(result["text"]) <= TEXT_WINDOW_BYTES
        pieces.append(result["text"])
        if result["next_offset"] is None:
            break
        assert offset < result["next_offset"] <= offset + TEXT_WINDOW_BYTES
        offset = result["next_offset"]
    assert "".join(pieces) == original


def test_docx_mid_end_windows_keep_memory_bounded_even_for_one_huge_paragraph(tmp_path):
    path = tmp_path / "huge.docx"
    # Two million characters in one XML text node: an ElementTree paragraph
    # accumulator would load it in full before being able to return page one.
    write_docx(path, "<w:p><w:r><w:t>" + "x" * 2_000_000 + "TAIL</w:t></w:r></w:p>")
    tracemalloc.start()
    start = docx_window(path, 0, max_uncompressed_bytes=BUDGET)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(start["blocks"]) == BLOCK_WINDOW
    assert start["next_offset"] == 40
    assert peak < 3_000_000
    tail = docx_window(path, 480, max_uncompressed_bytes=BUDGET)
    assert tail["next_offset"] is None
    assert tail["blocks"][-1]["text"].endswith("TAIL")


def test_docx_ten_thousand_rows_are_reachable_and_cells_are_explicitly_clipped(tmp_path):
    path = tmp_path / "rows.docx"
    rows = ''.join(f'<w:tr><w:tc><w:p><w:r><w:t>row-{i}</w:t></w:r></w:p></w:tc></w:tr>' for i in range(10000))
    write_docx(path, "<w:tbl>" + rows + "</w:tbl><w:p><w:r><w:t>FINAL</w:t></w:r></w:p>")
    middle = docx_window(path, 5000, max_uncompressed_bytes=BUDGET)
    assert middle["blocks"][0]["rows"] == [["row-5000"]]
    tail = docx_window(path, 9980, max_uncompressed_bytes=BUDGET)
    assert len(tail["blocks"]) == 21
    assert tail["blocks"][-1]["text"] == "FINAL"
    assert tail["next_offset"] is None
    write_docx(path, "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>" + "y" * 10000 + "</w:t></w:r></w:p></w:tc></w:tr></w:tbl>")
    clipped = docx_window(path, 0, max_uncompressed_bytes=BUDGET)
    assert clipped["clipped_cells"] is True
    assert len(clipped["blocks"][0]["rows"][0][0]) == CELL_CHARS


def test_docx_rejects_xml_entity_definitions_and_malformed_xml(tmp_path):
    path = tmp_path / "invalid.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY secret "unsafe">]><x>&secret;</x>')
    with pytest.raises(UnsupportedTextFileError, match="实体"):
        docx_window(path, 0, max_uncompressed_bytes=BUDGET)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<broken')
    with pytest.raises(UnsupportedTextFileError, match="损坏"):
        docx_window(path, 0, max_uncompressed_bytes=BUDGET)


def test_xlsx_reads_selected_sheet_rows_and_columns_and_reaches_tail(tmp_path):
    path = tmp_path / "rows.xlsx"
    book = Workbook()
    for i in range(1, 10001):
        book.active.append([f"row-{i}"] + [i] * 19)
    book.create_sheet("第二页").append(["other sheet", "z" * 10000])
    book.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    start = xlsx_window(path, 0, 0, 0, max_uncompressed_bytes=BUDGET)
    assert len(start["sheets"]) == 1
    assert len(start["sheets"][0]["rows"]) == 50
    assert len(start["sheets"][0]["columns"]) == 16
    assert start["next_column_offset"] == 16
    tail = xlsx_window(path, 0, 9950, 16, max_uncompressed_bytes=BUDGET)
    assert tail["next_offset"] is None
    assert tail["sheets"][0]["rows"][-1] == ["10000"] * 4
    other = xlsx_window(path, 1, 0, 0, max_uncompressed_bytes=BUDGET)
    assert other["sheets"][0]["rows"][0][0] == "other sheet"
    assert other["clipped_cells"] is True
    assert len(other["sheets"][0]["rows"][0][1]) == CELL_CHARS
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_image_preview_is_bounded_without_changing_the_original(tmp_path):
    path = tmp_path / "photo.png"
    Image.new("RGB", (4000, 3000), "white").save(path)
    before = path.read_bytes()
    preview = image_thumbnail(path, max_pixels=100_000_000)
    with Image.open(io.BytesIO(preview)) as result:
        assert result.size == (1600, 1200)
    assert path.read_bytes() == before
    with pytest.raises(UnsupportedTextFileError, match="像素"):
        image_thumbnail(path, max_pixels=1000)


def test_cancellation_is_observed_by_readers(tmp_path):
    cancelled = Event()
    cancelled.set()
    path = tmp_path / "cancel.txt"
    path.write_text("unused")
    with pytest.raises(UnsupportedTextFileError, match="关闭"):
        text_window(path, cancelled=cancelled)
    path = tmp_path / "cancel.docx"
    write_docx(path, "<w:p><w:r><w:t>unused</w:t></w:r></w:p>")
    with pytest.raises(UnsupportedTextFileError, match="关闭"):
        docx_window(path, 0, max_uncompressed_bytes=BUDGET, cancelled=cancelled)


def test_disconnect_cancels_background_preview_reader():
    stopped = Event()
    class Disconnected:
        async def is_disconnected(self):
            return True
    def reader(cancelled):
        while not cancelled.wait(0.01):
            pass
        stopped.set()
        return {}
    async def run():
        with pytest.raises(HTTPException) as error:
            await _read_preview(Disconnected(), reader)
        assert error.value.status_code == 499
    asyncio.run(run())
    assert stopped.wait(1)


def test_preview_routes_validate_windows_and_deleted_files(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        data = ("合同内容\n" * 10000).encode()
        upload = client.post("/api/v1/tasks", files={"file": ("large.txt", data, "text/plain")})
        assert upload.status_code == 201, upload.text
        task = upload.json()["id"]
        first = client.get(f"/api/v1/tasks/{task}/preview")
        assert first.status_code == 200
        assert len(first.content) < 110_000
        assert first.json()["next_offset"] is not None
        tail = client.get(f"/api/v1/tasks/{task}/preview", params={"offset": first.json()["next_offset"]})
        assert tail.status_code == 200
        assert client.get(f"/api/v1/tasks/{task}/preview?offset=-1").status_code == 422
        assert client.get(f"/api/v1/tasks/{task}/preview/xlsx").status_code == 415
        assert client.get(f"/api/v1/tasks/{task}/file").content == data
        assert client.post(f"/api/v1/tasks/{task}/cancel").status_code == 200
        assert client.delete(f"/api/v1/tasks/{task}").status_code in (200, 204)
        assert client.get(f"/api/v1/tasks/{task}/preview").status_code == 404
