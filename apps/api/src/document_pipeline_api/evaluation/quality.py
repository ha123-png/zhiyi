from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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

QualityRate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class GoldenSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    source_kind: Literal[
        "real_private", "public_benchmark", "synthetic", "unverified"
    ] = "unverified"
    fields: dict[str, str | float | bool | None]
    item_count: int = Field(ge=0)
    key_fields: list[str] = Field(default_factory=list)
    clarity: Literal["clear", "scan", "photo", "blurred", "handwritten", "mixed"] = (
        "clear"
    )
    page_count: int = Field(default=1, ge=1)

    @field_validator("fields")
    @classmethod
    def fields_must_use_finite_numbers(
        cls, fields: dict[str, str | float | bool | None]
    ) -> dict[str, str | float | bool | None]:
        unsupported_fields = set(fields) - (set(DocumentExtraction.model_fields) - {"items"})
        if unsupported_fields:
            raise ValueError("黄金字段必须对应单值提取字段。")
        if any(isinstance(value, float) and not math.isfinite(value) for value in fields.values()):
            raise ValueError("黄金字段不能包含非有限数值。")
        return fields


class GoldenDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    samples: dict[str, GoldenSample]


class SampleScore(BaseModel):
    sample_id: str
    category: str
    source_kind: Literal[
        "real_private", "public_benchmark", "synthetic", "unverified"
    ]
    parseable: bool
    classification_match: bool
    elapsed_seconds: NonNegativeFiniteFloat
    field_matches: dict[str, bool]
    key_field_matches: dict[str, bool] = Field(default_factory=dict)
    item_count_match: bool
    expected_item_count: int = Field(default=0, ge=0)
    actual_item_count: int = Field(default=0, ge=0)
    page_complete: bool = True
    clarity: str = "clear"
    page_count: int = Field(default=1, ge=1)
    document_exact: bool
    error_code: str | None = None
    classification_error_code: str | None = None


class MetricSlice(BaseModel):
    sample_count: int = Field(ge=0)
    parse_success_rate: QualityRate
    classification_accuracy: QualityRate
    field_accuracy: QualityRate
    item_count_accuracy: QualityRate
    document_exact_rate: QualityRate
    key_field_accuracy: QualityRate = 0
    detail_row_omission_rate: QualityRate = 0
    human_correction_rate: QualityRate = 0
    page_completeness_rate: QualityRate = 0


class QualityReport(BaseModel):
    report_version: int = 3
    latency_scope: Literal["legacy_pipeline_mixed", "extraction_only"] = (
        "legacy_pipeline_mixed"
    )
    golden_review_approved: bool = False
    key_field_coverage_complete: bool = False
    generated_at: datetime
    dataset_fingerprint: str
    golden_schema_version: int = Field(ge=1)
    model_provider: str
    model_name: str
    prompt_version: str
    overall: MetricSlice
    by_category: dict[str, MetricSlice]
    by_source_kind: dict[str, MetricSlice]
    by_clarity: dict[str, MetricSlice] = Field(default_factory=dict)
    by_page_count: dict[str, MetricSlice] = Field(default_factory=dict)
    latency_p50_seconds: NonNegativeFiniteFloat
    latency_p95_seconds: NonNegativeFiniteFloat
    samples: list[SampleScore]


class RegressionReport(BaseModel):
    comparable: bool
    regressions: list[str]


class QualityThresholds(BaseModel):
    """Release thresholds. Fixed gates are minimums and cannot be relaxed here."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    min_sample_count: int = Field(default=30, ge=30)
    min_real_private_samples: int = Field(default=30, ge=30)
    min_parse_success_rate: QualityRate = Field(default=1.0, ge=1.0)
    min_page_completeness_rate: QualityRate = Field(default=1.0, ge=1.0)
    min_classification_accuracy: QualityRate | None = None
    min_field_accuracy: QualityRate | None = None
    min_key_field_accuracy: QualityRate | None = None
    min_document_exact_rate: QualityRate | None = None
    max_detail_row_omission_rate: QualityRate | None = None
    max_human_correction_rate: QualityRate | None = None
    max_latency_p95_seconds: NonNegativeFiniteFloat | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    @field_validator("min_parse_success_rate", "min_page_completeness_rate")
    @classmethod
    def fixed_rate_hard_gates_are_one(cls, value: float) -> float:
        if value != 1.0:
            raise ValueError("固定成功率硬门必须为 1.0。")
        return value


class ReleaseGateReport(BaseModel):
    passed: bool
    blockers: list[str]


def load_golden(path: Path) -> GoldenDataset:
    return GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))


def _validated_sample_paths(dataset_dir: Path, golden: GoldenDataset) -> dict[str, Path]:
    root = dataset_dir.resolve()
    paths = {}
    for filename, sample in golden.samples.items():
        if sample.category not in CATEGORY_KIND:
            raise ValueError(f"黄金集包含不支持的类别：{sample.category}")
        if (
            not filename or filename in {".", ".."}
            or any(character in filename for character in ("/", "\\", ":"))
        ):
            raise ValueError("黄金样本名称必须是单个文件名。")
        path = (root / sample.category / filename).resolve()
        if not path.is_relative_to(root):
            raise ValueError("黄金样本路径超出数据集目录。")
        if not path.is_file():
            raise FileNotFoundError(f"黄金集缺少文件：{sample.category}/{filename}")
        paths[filename] = path
    return paths


def dataset_fingerprint(dataset_dir: Path, golden: GoldenDataset) -> str:
    return _dataset_fingerprint(golden, _validated_sample_paths(dataset_dir, golden))


def _dataset_fingerprint(golden: GoldenDataset, paths: dict[str, Path]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            golden.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for filename, sample in sorted(golden.samples.items()):
        digest.update(sample.category.encode("utf-8"))
        digest.update(filename.encode("utf-8"))
        with paths[filename].open("rb") as source:
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
    # Validate the entire corpus before passing even the first image to a provider.
    sample_paths = _validated_sample_paths(dataset_dir, golden)
    scores: list[SampleScore] = []
    templates = _builtin_template_reads()
    with tempfile.TemporaryDirectory(prefix="quality-evaluation-") as temp:
        rendered_root = Path(temp)
        for filename, expected in sorted(golden.samples.items()):
            kind = CATEGORY_KIND.get(expected.category)
            if kind is None:
                raise ValueError(f"黄金集包含不支持的类别：{expected.category}")
            path = sample_paths[filename]
            classification_match = False
            classification_error_code = None
            extraction_elapsed = 0.0
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
                extraction_started = time.perf_counter()
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
                finally:
                    extraction_elapsed = time.perf_counter() - extraction_started
                extraction = DocumentExtraction.model_validate(result.model_dump())
                field_matches = {
                    field: _values_match(getattr(extraction, field), value)
                    for field, value in expected.fields.items()
                }
                key_field_matches = {
                    field: field_matches.get(field, False)
                    for field in expected.key_fields
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
                        elapsed_seconds=round(extraction_elapsed, 3),
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
                        extraction_elapsed,
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
                        extraction_elapsed,
                        "evaluation_invalid_result",
                        classification_match,
                        classification_error_code,
                        page_complete=page_complete,
                    )
                )
    latencies = [score.elapsed_seconds for score in scores]
    return QualityReport(
        report_version=4,
        latency_scope="extraction_only",
        golden_review_approved=(
            golden.review_status == "approved"
            and bool(golden.reviewed_by and golden.reviewed_by.strip())
            and golden.reviewed_at is not None
            and golden.reviewed_at.tzinfo is not None
            and golden.reviewed_at.utcoffset() is not None
        ),
        key_field_coverage_complete=all(
            bool(sample.key_fields)
            and set(sample.key_fields).issubset(sample.fields)
            for sample in golden.samples.values()
        ),
        generated_at=datetime.now(timezone.utc),
        dataset_fingerprint=_dataset_fingerprint(golden, sample_paths),
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
    if (
        baseline.report_version != candidate.report_version
        or baseline.latency_scope != candidate.latency_scope
    ):
        return RegressionReport(
            comparable=False,
            regressions=["报告版本或耗时阶段口径不同，不能直接比较。"],
        )
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
    try:
        report = QualityReport.model_validate(report.model_dump(mode="python"))
    except ValidationError:
        return ReleaseGateReport(
            passed=False,
            blockers=["质量报告包含无效字段、非有限数值或超出范围的指标。"],
        )
    try:
        thresholds = QualityThresholds.model_validate(
            thresholds.model_dump(mode="python")
        )
    except ValidationError:
        return ReleaseGateReport(
            passed=False,
            blockers=["发布阈值包含无效字段、非有限数值或超出允许范围的值。"],
        )

    if report.report_version != 4 or report.latency_scope != "extraction_only":
        blockers.append("报告不是新版提取阶段口径；旧报告不会自动升级为发布证据。")
    if not report.golden_review_approved:
        blockers.append("黄金答案缺少业务人员签收状态、签收人或带时区的签收时间。")
    if not report.key_field_coverage_complete or any(
        not score.key_field_matches for score in report.samples
    ):
        blockers.append("至少一个样本缺少人工指定且覆盖黄金字段的关键字段。")
    if report.overall.sample_count != len(report.samples):
        blockers.append("报告样本总数与逐样本记录数不一致。")
    if report.overall != _metric_slice(report.samples):
        blockers.append("总体指标与逐样本记录不一致。")
    expected_summaries = {
        "by_category": {
            key: _metric_slice([score for score in report.samples if score.category == key])
            for key in sorted({score.category for score in report.samples})
        },
        "by_source_kind": {
            key: _metric_slice([score for score in report.samples if score.source_kind == key])
            for key in sorted({score.source_kind for score in report.samples})
        },
        "by_clarity": {
            key: _metric_slice([score for score in report.samples if score.clarity == key])
            for key in sorted({score.clarity for score in report.samples})
        },
        "by_page_count": {
            str(key): _metric_slice([score for score in report.samples if score.page_count == key])
            for key in sorted({score.page_count for score in report.samples})
        },
    }
    if any(
        getattr(report, field) != expected
        for field, expected in expected_summaries.items()
    ):
        blockers.append("分类或来源切片与逐样本记录不一致。")
    if any(score.source_kind == "unverified" for score in report.samples):
        blockers.append("至少一个样本缺少明确来源分类，不能计为真实脱敏样本。")
    if not report.dataset_fingerprint or len(report.dataset_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in report.dataset_fingerprint
    ):
        blockers.append("缺少有效的 SHA-256 数据集指纹。")
    if any(
        not value.strip()
        for value in (report.model_provider, report.model_name, report.prompt_version)
    ):
        blockers.append("缺少模型提供方式、模型名或提示版本。")
    if report.generated_at.tzinfo is None or report.generated_at.utcoffset() is None:
        blockers.append("质量报告生成时间缺少时区。")
    if (
        thresholds.approved_by is None
        or not thresholds.approved_by.strip()
        or thresholds.approved_at is None
        or thresholds.approved_at.tzinfo is None
        or thresholds.approved_at.utcoffset() is None
    ):
        blockers.append("发布阈值缺少人工批准人或带时区的批准时间。")

    optional_thresholds = (
        thresholds.min_classification_accuracy,
        thresholds.min_field_accuracy,
        thresholds.min_key_field_accuracy,
        thresholds.min_document_exact_rate,
        thresholds.max_detail_row_omission_rate,
        thresholds.max_human_correction_rate,
        thresholds.max_latency_p95_seconds,
    )
    if any(threshold is None for threshold in optional_thresholds):
        blockers.append("仍有首轮待决的可选质量阈值未填写。")

    real_samples = sum(
        score.source_kind == "real_private" for score in report.samples
    )
    fixed_hard_gates = {
        "min_sample_count": 30,
        "min_real_private_samples": 30,
        "min_parse_success_rate": 1.0,
        "min_page_completeness_rate": 1.0,
    }
    for field, minimum in fixed_hard_gates.items():
        if getattr(thresholds, field) < minimum:
            blockers.append(f"固定硬门 {field} 不能低于 {minimum}。")
    checks = (
        (report.overall.sample_count, max(30, thresholds.min_sample_count), "样本总数", ">="),
        (real_samples, max(30, thresholds.min_real_private_samples), "真实脱敏样本数", ">="),
        (
            report.overall.parse_success_rate,
            max(1.0, thresholds.min_parse_success_rate),
            "结构化输出成功率",
            ">=",
        ),
        (
            report.overall.page_completeness_rate,
            max(1.0, thresholds.min_page_completeness_rate),
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
    extraction_elapsed: float,
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
        elapsed_seconds=round(extraction_elapsed, 3),
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
