"""Each PDF sample must remain a distinct, bounded image at the model boundary."""

import io

import pypdfium2 as pdfium
from fastapi.testclient import TestClient
from PIL import Image

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.schemas.templates import TemplateDraft, TemplateDraftField


def _single_page_pdf(width, height):
    output = io.BytesIO()
    document = pdfium.PdfDocument.new()
    try:
        page = document.new_page(width, height)
        page.close()
        document.save(output)
    finally:
        document.close()
    return output.getvalue()


def _draft():
    return TemplateDraft(name="合成草稿", fields=[TemplateDraftField(
        key="f1", label="字段", section="header", example="", value_type="text",
    )])


def test_multiple_pdf_samples_reach_draft_generation_in_original_order(tmp_path, monkeypatch):
    observed = []

    def generate(*args, image_paths, **kwargs):
        for path in image_paths:
            with Image.open(path) as image:
                observed.append(image.size)
        return _draft()

    monkeypatch.setattr("document_pipeline_api.api.templates.generate_template_draft", generate)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'synthetic.db'}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/templates/generate", data={"requirement": "两份合成样例"}, files=[
            ("files", ("first.pdf", _single_page_pdf(10, 12), "application/pdf")),
            ("files", ("second.pdf", _single_page_pdf(20, 15), "application/pdf")),
        ])
    assert response.status_code == 200
    assert observed == [(30, 36), (60, 45)]


def test_pdf_sample_rejects_render_pixels_above_configured_budget_before_model(tmp_path, monkeypatch):
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        return _draft()

    monkeypatch.setattr("document_pipeline_api.api.templates.generate_template_draft", generate)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'synthetic.db'}", storage_dir=tmp_path / "uploads", max_image_total_pixels=10)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/templates/generate", data={"requirement": "小预算合成样例"}, files=[
            ("files", ("small.pdf", _single_page_pdf(2, 3), "application/pdf")),
        ])
    assert response.status_code == 422
    assert "像素" in response.json()["detail"]
    assert calls == []
