import time
from fastapi.testclient import TestClient
import pytest

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app

from document_pipeline_api.domain.pattern_matching import matches_pattern
from document_pipeline_api.domain.template_rules import validate_template_rules
from document_pipeline_api.schemas.extraction import TemplateExtraction
from document_pipeline_api.schemas.rules import PatternRule


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_dir=tmp_path / "uploads")
    with TestClient(create_app(settings)) as client:
        yield client


def test_preview_uses_full_match_and_rejects_unsupported_patterns(client):
    for text, matched in [("PO-001", True), ("PO-01", False), ("编号 PO-001", False)]:
        response = client.post("/api/v1/templates/rules/preview-pattern", json={"pattern": r"PO-\d{3}", "text": text})
        assert response.status_code == 200
        assert response.json()["matches"] is matched
    response = client.post("/api/v1/templates/rules/preview-pattern", json={"pattern": "(a+)+", "text": "aaa"})
    assert response.json()["valid"] is False


def test_ambiguous_repeat_is_bounded_and_reported_without_skipping_silently():
    rule = PatternRule(kind="pattern", field="items[].code", pattern="a*" * 40 + "b")
    result = TemplateExtraction(header={}, items=[{"code": "a" * 512}] * 100)
    started = time.monotonic()
    issues = validate_template_rules(result, [rule], [])
    assert time.monotonic() - started < 2
    assert issues


def test_timeout_stops_remaining_rows_for_that_rule(monkeypatch):
    calls = []
    def slow(*args):
        calls.append(args)
        raise TimeoutError
    monkeypatch.setattr("document_pipeline_api.domain.template_rules.matches_pattern", slow)
    rule = PatternRule(kind="pattern", field="items[].code", pattern="a*")
    issues = validate_template_rules(TemplateExtraction(header={}, items=[{"code": "a"}] * 100), [rule], [])
    assert len(calls) == 1
    assert "已停止" in issues[0].message
    assert issues[0].severity == "error"


def test_pattern_preserves_unicode_digits_and_rejects_long_input():
    assert matches_pattern(r"\d{3}", "１２３")
    assert not matches_pattern(".*", "a" * 513)
