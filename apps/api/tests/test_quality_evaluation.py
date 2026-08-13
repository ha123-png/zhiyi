from pathlib import Path
import io

from PIL import Image

from document_pipeline_api.evaluation.quality import (
    GoldenDataset,
    QualityReport,
    QualityThresholds,
    compare_quality_reports,
    dataset_fingerprint,
    evaluate_release_gate,
    run_quality_evaluation,
)
from document_pipeline_api.model_providers.base import ModelTimeoutError
from image_test_data import PNG_BYTES


class StubQualityProvider:
    model_name = "quality-model"

    def extract_image(self, path: Path, _prompt: str, result_type):
        if path.name == "timeout.png":
            raise ModelTimeoutError("sensitive provider detail")
        if result_type.__name__ == "TemplateMatchDecision":
            template_id = (
                "builtin-invoice" if path.name == "invoice.png" else "builtin-delivery"
            )
            return result_type.model_validate(
                {"outcome": "matched", "template_ids": [template_id]}
            )
        buyer = "正确买方" if path.name == "invoice.png" else "错误买方"
        return result_type.model_validate(
            {
                "document_type": "测试",
                "seller_name": "正确卖方",
                "buyer_name": buyer,
                "document_number": None,
                "document_date": None,
                "amount_before_tax": None,
                "tax_amount": None,
                "total_amount": 10,
                "items": [],
            }
        )


class MultiPageQualityProvider:
    model_name = "multi-page-model"

    def __init__(self) -> None:
        self.extraction_page_counts: list[int] = []

    def extract_image(self, _path: Path, _prompt: str, result_type):
        if result_type.__name__ == "TemplateMatchDecision":
            return result_type.model_validate(
                {"outcome": "matched", "template_ids": ["builtin-invoice"]}
            )
        raise AssertionError("两页样本不应退化为单页提取。")

    def extract_images(self, paths: list[Path], _prompt: str, result_type):
        self.extraction_page_counts.append(len(paths))
        return result_type.model_validate(
            {
                "document_type": "发票",
                "seller_name": "正确卖方",
                "buyer_name": "正确买方",
                "document_number": None,
                "document_date": None,
                "amount_before_tax": None,
                "tax_amount": None,
                "total_amount": 10,
                "items": [],
            }
        )


def _golden() -> GoldenDataset:
    return GoldenDataset.model_validate(
        {
            "schema_version": 1,
            "review_status": "checked",
            "samples": {
                "invoice.png": {
                    "category": "发票",
                    "source_kind": "real_private",
                    "fields": {
                        "seller_name": "正确卖方",
                        "buyer_name": "正确买方",
                    },
                    "item_count": 0,
                    "key_fields": ["seller_name", "buyer_name"],
                    "clarity": "clear",
                },
                "delivery.png": {
                    "category": "送货单",
                    "source_kind": "synthetic",
                    "fields": {
                        "seller_name": "正确卖方",
                        "buyer_name": "正确买方",
                    },
                    "item_count": 0,
                    "key_fields": ["seller_name", "buyer_name"],
                    "clarity": "photo",
                },
                "timeout.png": {
                    "category": "送货单",
                    "source_kind": "synthetic",
                    "fields": {
                        "seller_name": "不应进入报告",
                        "buyer_name": "不应进入报告",
                    },
                    "item_count": 1,
                    "key_fields": ["seller_name", "buyer_name"],
                    "clarity": "blurred",
                },
            },
        }
    )


def test_quality_report_is_reproducible_and_contains_no_business_values(
    tmp_path: Path,
) -> None:
    golden = _golden()
    for filename, sample in golden.samples.items():
        directory = tmp_path / sample.category
        directory.mkdir(exist_ok=True)
        (directory / filename).write_bytes(PNG_BYTES)

    first_fingerprint = dataset_fingerprint(tmp_path, golden)
    report = run_quality_evaluation(
        StubQualityProvider(),
        provider_name="lm_studio",
        dataset_dir=tmp_path,
        golden=golden,
    )

    assert report.dataset_fingerprint == first_fingerprint
    assert report.overall.sample_count == 3
    assert report.overall.parse_success_rate == 2 / 3
    assert report.overall.classification_accuracy == 2 / 3
    assert report.overall.field_accuracy == 3 / 6
    assert report.overall.item_count_accuracy == 2 / 3
    assert report.overall.document_exact_rate == 1 / 3
    assert report.overall.key_field_accuracy == 3 / 6
    assert report.overall.detail_row_omission_rate == 1
    assert report.overall.human_correction_rate == 2 / 3
    assert report.overall.page_completeness_rate == 1
    assert set(report.by_clarity) == {"blurred", "clear", "photo"}
    assert report.by_page_count["1"].sample_count == 3
    assert report.by_category["发票"].document_exact_rate == 1
    assert report.by_category["送货单"].document_exact_rate == 0
    assert report.by_source_kind["real_private"].document_exact_rate == 1
    assert report.by_source_kind["synthetic"].document_exact_rate == 0
    assert report.samples[2].error_code == "model_timeout"
    serialized = report.model_dump_json()
    assert "正确卖方" not in serialized
    assert "不应进入报告" not in serialized
    assert "sensitive provider detail" not in serialized
    assert "invoice.png" not in serialized

    (tmp_path / "发票" / "invoice.png").write_bytes(b"changed")
    assert dataset_fingerprint(tmp_path, golden) != first_fingerprint


def test_regression_gate_rejects_metric_drop_and_dataset_mismatch() -> None:
    baseline = QualityReport.model_validate(
        {
            "generated_at": "2026-08-02T00:00:00Z",
            "dataset_fingerprint": "same",
            "golden_schema_version": 1,
            "model_provider": "lm_studio",
            "model_name": "model-a",
            "prompt_version": "v1",
            "overall": {
                "sample_count": 2,
                "parse_success_rate": 1,
                "classification_accuracy": 1,
                "field_accuracy": 1,
                "item_count_accuracy": 1,
                "document_exact_rate": 1,
            },
            "by_category": {},
            "by_source_kind": {},
            "latency_p50_seconds": 1,
            "latency_p95_seconds": 2,
            "samples": [],
        }
    )
    candidate = baseline.model_copy(deep=True)
    candidate.overall.document_exact_rate = 0.5

    comparison = compare_quality_reports(baseline, candidate)

    assert comparison.comparable is True
    assert comparison.regressions == ["整文档完全正确率从 1.0000 降至 0.5000。"]
    mismatched = candidate.model_copy(update={"dataset_fingerprint": "different"})
    assert compare_quality_reports(baseline, mismatched).comparable is False


def test_release_gate_enforces_hard_and_user_defined_thresholds() -> None:
    report = QualityReport.model_validate(
        {
            "generated_at": "2026-08-02T00:00:00Z",
            "dataset_fingerprint": "same",
            "golden_schema_version": 2,
            "model_provider": "lm_studio",
            "model_name": "model-a",
            "prompt_version": "v1",
            "overall": {
                "sample_count": 1,
                "parse_success_rate": 1,
                "classification_accuracy": 0.9,
                "field_accuracy": 0.95,
                "item_count_accuracy": 0.9,
                "document_exact_rate": 0.8,
                "key_field_accuracy": 0.98,
                "detail_row_omission_rate": 0.02,
                "human_correction_rate": 0.2,
                "page_completeness_rate": 1,
            },
            "by_category": {},
            "by_source_kind": {},
            "latency_p50_seconds": 1,
            "latency_p95_seconds": 3,
            "samples": [
                {
                    "sample_id": "private",
                    "category": "发票",
                    "source_kind": "real_private",
                    "parseable": True,
                    "classification_match": True,
                    "elapsed_seconds": 1,
                    "field_matches": {"seller_name": True},
                    "key_field_matches": {"seller_name": True},
                    "item_count_match": True,
                    "document_exact": True,
                }
            ],
        }
    )
    gate = evaluate_release_gate(
        report,
        QualityThresholds(
            min_sample_count=30,
            min_real_private_samples=30,
            min_key_field_accuracy=0.99,
            max_detail_row_omission_rate=0.01,
            max_latency_p95_seconds=2,
        ),
    )

    assert gate.passed is False
    assert any("样本总数" in blocker for blocker in gate.blockers)
    assert any("关键字段准确率" in blocker for blocker in gate.blockers)
    assert any("明细漏行率" in blocker for blocker in gate.blockers)
    assert any("P95" in blocker for blocker in gate.blockers)


def test_quality_evaluation_uses_all_frames_and_checks_page_completeness(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "发票"
    directory.mkdir()
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])
    (directory / "two-pages.tiff").write_bytes(buffer.getvalue())
    golden = GoldenDataset.model_validate(
        {
            "schema_version": 2,
            "review_status": "checked",
            "samples": {
                "two-pages.tiff": {
                    "category": "发票",
                    "source_kind": "real_private",
                    "fields": {
                        "seller_name": "正确卖方",
                        "buyer_name": "正确买方",
                    },
                    "key_fields": ["seller_name"],
                    "item_count": 0,
                    "page_count": 2,
                    "clarity": "scan",
                }
            },
        }
    )
    provider = MultiPageQualityProvider()

    report = run_quality_evaluation(
        provider,
        provider_name="lm_studio",
        dataset_dir=tmp_path,
        golden=golden,
    )

    assert provider.extraction_page_counts == [2]
    assert report.overall.page_completeness_rate == 1
    assert report.overall.document_exact_rate == 1
    assert report.by_page_count["2"].sample_count == 1
