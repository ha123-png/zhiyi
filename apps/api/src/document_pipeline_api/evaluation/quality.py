from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from document_pipeline_api.model_providers.base import ModelProvider, ModelServiceError
from document_pipeline_api.schemas.extraction import DocumentExtraction, DocumentKind
from document_pipeline_api.schemas.templates import TemplateRead
from document_pipeline_api.services.extraction import EXTRACTION_PROMPTS, PROMPT_VERSION
from document_pipeline_api.services.file_formats import (
    inspect_image_frame_count,
    render_image_frames,
)
from document_pipeline_api.services.pdf_rendering import render_pdf_pages
from document_pipeline_api.services.template_processing import match_template
from document_pipeline_api.services.templates import BUILTIN_TEMPLATES


CATEGORY_KIND = {
    "发票": DocumentKind.INVOICE,
    "送货单": DocumentKind.DELIVERY,
}
CATEGORY_TEMPLATE_ID = {
    "发票": "builtin-invoice",
    "送货单": "builtin-delivery",
}


class GoldenSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    source_kind: Literal["real_private", "public_benchmark", "synthetic"] = (
        "real_private"
    )
    fields: dict[str, str | float | bool | None]
    item_count: int
    key_fields: list[str] = Field(default_factory=list)
    clarity: Literal["clear", "scan", "photo", "blurred", "handwritten", "mixed"] = (
        "clear"
    )
    page_count: int = Field(default=1, ge=1)


class GoldenDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    review_status: str
    samples: dict[str, GoldenSample]


class SampleScore(BaseModel):
    sample_id: str
    category: str
    source_kind: Literal["real_private", "public_benchmark", "synthetic"]
    parseable: bool
    classification_match: bool
    elapsed_seconds: float
    field_matches: dict[str, bool]
    key_field_matches: dict[str, bool] = Field(default_factory=dict)
    item_count_match: bool
    expected_item_count: int = 0
    actual_item_count: int = 0
    page_complete: bool = True
    clarity: str = "clear"
    page_count: int = 1
    document_exact: bool
    error_code: str | None = None
    classification_error_code: str | None = None


class MetricSlice(BaseModel):
    sample_count: int
    parse_success_rate: float
    classification_accuracy: float
    field_accuracy: float
    item_count_accuracy: float
    document_exact_rate: float
    key_field_accuracy: float = 0
    detail_row_omission_rate: float = 0
    human_correction_rate: float = 0
    page_completeness_rate: float = 0


class QualityReport(BaseModel):
    report_version: int = 3
    generated_at: datetime
    dataset_fingerprint: str
    golden_schema_version: int
    model_provider: str
    model_name: str
    prompt_version: str
    overall: MetricSlice
    by_category: dict[str, MetricSlice]
    by_source_kind: dict[str, MetricSlice]
    by_clarity: dict[str, MetricSlice] = Field(default_factory=dict)
    by_page_count: dict[str, MetricSlice] = Field(default_factory=dict)
    latency_p50_seconds: float
    latency_p95_seconds: float
    samples: list[SampleScore]


class RegressionReport(BaseModel):
    comparable: bool
    regressions: list[str]


class QualityThresholds(BaseModel):
    """发布阈值；只有结构化成功与页完整是固定硬门，其余由用户首轮拍板。"""

    min_sample_count: int = 30
    min_real_private_samples: int = 30
    min_parse_success_rate: float = 1.0
    min_page_completeness_rate: float = 1.0
    min_classification_accuracy: float | None = None
    min_field_accuracy: float | None = None
    min_key_field_accuracy: float | None = None
    min_document_exact_rate: float | None = None
    max_detail_row_omission_rate: float | None = None
    max_human_correction_rate: float | None = None
    max_latency_p95_seconds: float | None = None


class ReleaseGateReport(BaseModel):
    passed: bool
    blockers: list[str]


def load_golden(path: Path) -> GoldenDataset:
    return GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))


def dataset_fingerprint(dataset_dir: Path, golden: GoldenDataset) -> str:
    digest = hashlib.sha256(
        json.dumps(
            golden.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for filename, sample in sorted(golden.samples.items()):
        path = dataset_dir / sample.category / filename
        if not path.is_file():
            raise FileNotFoundError(f"黄金集缺少文件：{sample.category}/{filename}")
        digest.update(sample.category.encode("utf-8"))
        digest.update(filename.encode("utf-8"))
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def run_quality_evaluation(
    provider: ModelProvider,
    *,
    provider_name: str,
    dataset_dir: Path,
    golden: GoldenDataset,
) -> QualityReport:
    scores: list[SampleScore] = []
    templates = _builtin_template_reads()
    with tempfile.TemporaryDirectory(prefix="quality-evaluation-") as temp:
        rendered_root = Path(temp)
        for filename, expected in sorted(golden.samples.items()):
            kind = CATEGORY_KIND.get(expected.category)
            if kind is None:
                raise ValueError(f"黄金集包含不支持的类别：{expected.category}")
            path = dataset_dir / expected.category / filename
            classification_match = False
            classification_error_code = None
            started = time.perf_counter()
            try:
                image_paths = _evaluation_pages(
                    path,
                    rendered_root / _sample_id(filename),
                    max_pages=max(expected.page_count, 100),
                )
                page_complete = len(image_paths) == expected.page_count
            except (HTTPException, OSError, ValueError, TypeError):
                image_paths = [path]
                page_complete = False
            try:
                decision = match_template(image_paths[0], templates, provider)
                classification_match = (
                    decision.outcome == "matched"
                    and decision.template_ids == [CATEGORY_TEMPLATE_ID[expected.category]]
                )
            except ModelServiceError as error:
                classification_error_code = error.code
            except (HTTPException, OSError, ValueError, TypeError):
                classification_error_code = "classification_invalid_result"
            try:
                result = (
                    provider.extract_image(
                        image_paths[0],
                        EXTRACTION_PROMPTS[kind],
                        DocumentExtraction,
                    )
                    if len(image_paths) == 1
                    else provider.extract_images(
                        image_paths,
                        EXTRACTION_PROMPTS[kind],
                        DocumentExtraction,
                    )
                )
                extraction = DocumentExtraction.model_validate(result.model_dump())
                field_matches = {
                    field: _values_match(getattr(extraction, field), value)
                    for field, value in expected.fields.items()
                }
                key_field_matches = {
                    field: field_matches[field]
                    for field in expected.key_fields
                    if field in field_matches
                }
                actual_item_count = len(extraction.items)
                item_count_match = actual_item_count == expected.item_count
                scores.append(
                    SampleScore(
                        sample_id=_sample_id(filename),
                        category=expected.category,
                        source_kind=expected.source_kind,
                        parseable=True,
                        classification_match=classification_match,
                        elapsed_seconds=round(time.perf_counter() - started, 3),
                        field_matches=field_matches,
                        key_field_matches=key_field_matches,
                        item_count_match=item_count_match,
                        expected_item_count=expected.item_count,
                        actual_item_count=actual_item_count,
                        page_complete=page_complete,
                        clarity=expected.clarity,
                        page_count=expected.page_count,
                        document_exact=(
                            all(field_matches.values())
                            and item_count_match
                            and page_complete
                        ),
                        classification_error_code=classification_error_code,
                    )
                )
            except ModelServiceError as error:
                scores.append(
                    _failed_score(
                        filename,
                        expected,
                        started,
                        error.code,
                        classification_match,
                        classification_error_code,
                        page_complete=page_complete,
                    )
                )
            except (OSError, ValueError, TypeError):
                scores.append(
                    _failed_score(
                        filename,
                        expected,
                        started,
                        "evaluation_invalid_result",
                        classification_match,
                        classification_error_code,
                        page_complete=page_complete,
                    )
                )
    latencies = [score.elapsed_seconds for score in scores]
    return QualityReport(
        generated_at=datetime.now(timezone.utc),
        dataset_fingerprint=dataset_fingerprint(dataset_dir, golden),
        golden_schema_version=golden.schema_version,
        model_provider=provider_name,
        model_name=provider.model_name,
        prompt_version=PROMPT_VERSION,
        overall=_metric_slice(scores),
        by_category={
            category: _metric_slice([score for score in scores if score.category == category])
            for category in sorted({score.category for score in scores})
        },
        by_source_kind={
            source_kind: _metric_slice(
                [score for score in scores if score.source_kind == source_kind]
            )
            for source_kind in sorted({score.source_kind for score in scores})
        },
        by_clarity={
            clarity: _metric_slice([score for score in scores if score.clarity == clarity])
            for clarity in sorted({score.clarity for score in scores})
        },
        by_page_count={
            str(page_count): _metric_slice(
                [score for score in scores if score.page_count == page_count]
            )
            for page_count in sorted({score.page_count for score in scores})
        },
        latency_p50_seconds=_percentile(latencies, 0.5),
        latency_p95_seconds=_percentile(latencies, 0.95),
        samples=scores,
    )


def compare_quality_reports(
    baseline: QualityReport,
    candidate: QualityReport,
) -> RegressionReport:
    if baseline.dataset_fingerprint != candidate.dataset_fingerprint:
        return RegressionReport(
            comparable=False,
            regressions=["黄金集指纹不同，不能把两个报告当作同一基线比较。"],
        )
    regressions = []
    for field, label in (
        ("parse_success_rate", "解析成功率"),
        ("classification_accuracy", "智能匹配准确率"),
        ("field_accuracy", "黄金字段准确率"),
        ("key_field_accuracy", "关键字段准确率"),
        ("item_count_accuracy", "明细数量准确率"),
        ("document_exact_rate", "整文档完全正确率"),
        ("page_completeness_rate", "页数完整率"),
    ):
        before = getattr(baseline.overall, field)
        after = getattr(candidate.overall, field)
        if after < before:
            regressions.append(f"{label}从 {before:.4f} 降至 {after:.4f}。")
    for field, label in (
        ("detail_row_omission_rate", "明细漏行率"),
        ("human_correction_rate", "人工修正率"),
    ):
        before = getattr(baseline.overall, field)
        after = getattr(candidate.overall, field)
        if after > before:
            regressions.append(f"{label}从 {before:.4f} 升至 {after:.4f}。")
    return RegressionReport(comparable=True, regressions=regressions)


def evaluate_release_gate(
    report: QualityReport,
    thresholds: QualityThresholds,
) -> ReleaseGateReport:
    blockers: list[str] = []
    real_samples = sum(
        score.source_kind == "real_private" for score in report.samples
    )
    checks = (
        (report.overall.sample_count, thresholds.min_sample_count, "样本总数", ">="),
        (real_samples, thresholds.min_real_private_samples, "真实脱敏样本数", ">="),
        (
            report.overall.parse_success_rate,
            thresholds.min_parse_success_rate,
            "结构化输出成功率",
            ">=",
        ),
        (
            report.overall.page_completeness_rate,
            thresholds.min_page_completeness_rate,
            "页数完整率",
            ">=",
        ),
    )
    for actual, expected, label, _operator in checks:
        if actual < expected:
            blockers.append(f"{label} {actual:.4g} 低于门槛 {expected:.4g}。")
    for field, threshold, label in (
        ("classification_accuracy", thresholds.min_classification_accuracy, "分类准确率"),
        ("field_accuracy", thresholds.min_field_accuracy, "字段准确率"),
        ("key_field_accuracy", thresholds.min_key_field_accuracy, "关键字段准确率"),
        ("document_exact_rate", thresholds.min_document_exact_rate, "整文档正确率"),
    ):
        actual = getattr(report.overall, field)
        if threshold is not None and actual < threshold:
            blockers.append(f"{label} {actual:.4f} 低于门槛 {threshold:.4f}。")
    for field, threshold, label in (
        (
            "detail_row_omission_rate",
            thresholds.max_detail_row_omission_rate,
            "明细漏行率",
        ),
        (
            "human_correction_rate",
            thresholds.max_human_correction_rate,
            "人工修正率",
        ),
    ):
        actual = getattr(report.overall, field)
        if threshold is not None and actual > threshold:
            blockers.append(f"{label} {actual:.4f} 高于门槛 {threshold:.4f}。")
    if (
        thresholds.max_latency_p95_seconds is not None
        and report.latency_p95_seconds > thresholds.max_latency_p95_seconds
    ):
        blockers.append(
            f"P95 耗时 {report.latency_p95_seconds:.3f}s 高于门槛 "
            f"{thresholds.max_latency_p95_seconds:.3f}s。"
        )
    return ReleaseGateReport(passed=not blockers, blockers=blockers)


def _evaluation_pages(path: Path, rendered_dir: Path, *, max_pages: int) -> list[Path]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return render_pdf_pages(path, rendered_dir, max_pages=max_pages)
    content_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix)
    if content_type is None:
        raise ValueError(f"质量评测暂不支持文件格式：{suffix or '无扩展名'}")
    frame_count = inspect_image_frame_count(
        path,
        expected_content_type=content_type,
        max_frames=max_pages,
        max_total_pixels=1_000_000_000,
    )
    if frame_count == 1 and content_type in {"image/png", "image/jpeg"}:
        return [path]
    return render_image_frames(
        path,
        rendered_dir,
        _sample_id(path.name),
        max_frames=max_pages,
        expected_content_type=content_type,
        max_total_pixels=1_000_000_000,
    )


def _failed_score(
    filename: str,
    expected: GoldenSample,
    started: float,
    error_code: str,
    classification_match: bool,
    classification_error_code: str | None,
    *,
    page_complete: bool,
) -> SampleScore:
    return SampleScore(
        sample_id=_sample_id(filename),
        category=expected.category,
        source_kind=expected.source_kind,
        parseable=False,
        classification_match=classification_match,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        field_matches={field: False for field in expected.fields},
        key_field_matches={field: False for field in expected.key_fields},
        item_count_match=False,
        expected_item_count=expected.item_count,
        actual_item_count=0,
        page_complete=page_complete,
        clarity=expected.clarity,
        page_count=expected.page_count,
        document_exact=False,
        error_code=error_code,
        classification_error_code=classification_error_code,
    )


def _metric_slice(scores: list[SampleScore]) -> MetricSlice:
    if not scores:
        return MetricSlice(
            sample_count=0,
            parse_success_rate=0,
            classification_accuracy=0,
            field_accuracy=0,
            item_count_accuracy=0,
            document_exact_rate=0,
        )
    field_matches = [match for score in scores for match in score.field_matches.values()]
    key_field_matches = [
        match for score in scores for match in score.key_field_matches.values()
    ]
    expected_items = sum(score.expected_item_count for score in scores)
    missing_items = sum(
        max(score.expected_item_count - score.actual_item_count, 0) for score in scores
    )
    return MetricSlice(
        sample_count=len(scores),
        parse_success_rate=sum(score.parseable for score in scores) / len(scores),
        classification_accuracy=(
            sum(score.classification_match for score in scores) / len(scores)
        ),
        field_accuracy=sum(field_matches) / len(field_matches) if field_matches else 0,
        item_count_accuracy=sum(score.item_count_match for score in scores) / len(scores),
        document_exact_rate=sum(score.document_exact for score in scores) / len(scores),
        key_field_accuracy=(
            sum(key_field_matches) / len(key_field_matches) if key_field_matches else 0
        ),
        detail_row_omission_rate=(missing_items / expected_items if expected_items else 0),
        human_correction_rate=(
            sum(not score.document_exact for score in scores) / len(scores)
        ),
        page_completeness_rate=(
            sum(score.page_complete for score in scores) / len(scores)
        ),
    )


def _sample_id(filename: str) -> str:
    return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]


def _values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and abs(float(actual) - float(expected)) < 0.01
        )
    return actual == expected


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 3)


def _builtin_template_reads() -> list[TemplateRead]:
    now = datetime.now(timezone.utc)
    return [
        TemplateRead.model_validate(
            {
                **definition,
                "version": 1,
                "is_system": True,
                "is_active": True,
                "source_template_id": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        for definition in BUILTIN_TEMPLATES
    ]
