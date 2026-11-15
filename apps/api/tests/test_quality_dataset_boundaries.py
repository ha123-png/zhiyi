"""Dataset metadata must not select documents outside the chosen corpus."""

from pathlib import Path

import pytest

from document_pipeline_api.evaluation.quality import (
    GoldenDataset, dataset_fingerprint, run_quality_evaluation,
)
from image_test_data import PNG_BYTES
from test_quality_evaluation import StubQualityProvider


class RecordingProvider(StubQualityProvider):
    def __init__(self):
        self.paths = []

    def extract_image(self, path, prompt, result_type):
        self.paths.append(path)
        return super().extract_image(path, prompt, result_type)


def golden_for(*filenames):
    return GoldenDataset.model_validate({
        "schema_version": 1,
        "review_status": "fixture",
        "samples": {
            name: {"category": "发票", "source_kind": "synthetic", "fields": {}, "item_count": 0}
            for name in filenames
        },
    })


@pytest.mark.parametrize("kind", ["traversal", "absolute", "windows_separator"])
def test_untrusted_sample_name_is_rejected_before_any_provider_call(tmp_path, kind):
    root = tmp_path / "dataset"
    category = root / "发票"
    (category / "z").mkdir(parents=True)
    (category / "a.png").write_bytes(PNG_BYTES)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    bad_name = {
        "traversal": "z/../../../outside.png",
        "absolute": str(outside),
        "windows_separator": r"z\..\..\..\outside.png",
    }[kind]
    golden = golden_for("a.png", bad_name)
    provider = RecordingProvider()

    with pytest.raises(ValueError):
        run_quality_evaluation(provider, provider_name="fixture", dataset_dir=root, golden=golden)
    assert provider.paths == []
    with pytest.raises(ValueError):
        dataset_fingerprint(root, golden)


def test_resolved_sample_outside_root_is_rejected_before_any_provider_call(tmp_path, monkeypatch):
    root = tmp_path / "dataset"
    category = root / "发票"
    category.mkdir(parents=True)
    sample = category / "invoice.png"
    sample.write_bytes(PNG_BYTES)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    original_resolve = Path.resolve

    def linked_sample(self, *args, **kwargs):
        if self == sample:
            return original_resolve(outside, *args, **kwargs)
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", linked_sample)
    golden = golden_for("invoice.png")
    provider = RecordingProvider()
    with pytest.raises(ValueError):
        run_quality_evaluation(provider, provider_name="fixture", dataset_dir=root, golden=golden)
    assert provider.paths == []
    with pytest.raises(ValueError):
        dataset_fingerprint(root, golden)


def test_missing_sample_is_detected_before_any_provider_call(tmp_path):
    root = tmp_path / "dataset"
    category = root / "发票"
    category.mkdir(parents=True)
    (category / "a.png").write_bytes(PNG_BYTES)
    provider = RecordingProvider()
    with pytest.raises(FileNotFoundError):
        run_quality_evaluation(
            provider, provider_name="fixture", dataset_dir=root,
            golden=golden_for("a.png", "z-missing.png"),
        )
    assert provider.paths == []


@pytest.mark.parametrize("filename", ["invoice.png", "采购单据.png"])
def test_a_normal_sample_still_evaluates_and_fingerprints(tmp_path, filename):
    root = tmp_path / "dataset"
    category = root / "发票"
    category.mkdir(parents=True)
    (category / filename).write_bytes(PNG_BYTES)
    golden = golden_for(filename)
    provider = RecordingProvider()
    report = run_quality_evaluation(provider, provider_name="fixture", dataset_dir=root, golden=golden)
    assert len(provider.paths) == 2
    assert report.overall.sample_count == 1
    assert report.dataset_fingerprint == dataset_fingerprint(root, golden)
