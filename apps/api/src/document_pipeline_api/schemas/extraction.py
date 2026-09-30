from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from document_pipeline_api.schemas.templates import TemplateRead
from document_pipeline_api.schemas.input_scope import InputScope, SourceRange
from document_pipeline_api.schemas.file_name import FileNameRead
from document_pipeline_api.schemas.json_numbers import stringify_unsafe_integers


class DocumentKind(StrEnum):
    INVOICE = "invoice"
    DELIVERY = "delivery"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class DocumentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_kind: DocumentKind


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None
    specification: str | None
    unit: str | None
    quantity: int | float | None
    unit_price: int | float | None
    amount: int | float | None
    tax_rate: str | None
    tax_amount: int | float | None
    remarks: str | None = None


class DocumentExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str | None
    seller_name: str | None
    buyer_name: str | None
    seller_tax_id: str | None = None
    buyer_tax_id: str | None = None
    seller_contact: str | None = None
    buyer_contact: str | None = None
    buyer_address: str | None = None
    document_number: str | None
    document_date: str | None
    amount_before_tax: int | float | None
    tax_amount: int | float | None
    total_amount: int | float | None
    total_quantity: int | float | None = None
    remarks: str | None = None
    items: list[LineItem]


TemplateScalar = str | int | float | bool | None


class TemplateExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: dict[str, TemplateScalar]
    items: list[dict[str, TemplateScalar]]


class ValidationIssue(BaseModel):
    code: str
    field: str
    message: str
    severity: Literal["warning", "error"]
    # 用户可显式忽略（保持审计语义：被忽略的问题仍记录，但不再阻断确认）
    ignored: bool = False


class EvidenceRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class FieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    page_number: int | None = Field(default=None, ge=1)
    region: EvidenceRegion | None = None
    quote: str | None = Field(default=None, max_length=256)
    status: Literal["page_only", "located", "unavailable", "user_edited"]
    source: Literal["system", "model_reported", "user"]
    location_verified: bool
    location: SourceRange | None = None


class ExtractionRead(BaseModel):
    current_schema: list[dict] = Field(default_factory=list)
    detail_ids: list[int] = Field(default_factory=list)
    has_current_data: bool = True
    file_name: FileNameRead | None = None
    task_id: str
    document_kind: DocumentKind
    template_id: str | None
    template_version: int | None
    template: TemplateRead | None
    model_name: str
    prompt_version: str
    elapsed_seconds: float
    review_version: int
    original_result: DocumentExtraction | TemplateExtraction
    result: DocumentExtraction | TemplateExtraction
    validation_issues: list[ValidationIssue]
    evidence: list[FieldEvidence]
    input_scope: InputScope | None = None

    @field_serializer("original_result", "result", when_used="json")
    def preserve_unsafe_integer_tokens(self, value):
        return stringify_unsafe_integers(value.model_dump(mode="python"))


class ReviewUpdate(BaseModel):
    # Stable identities survive deletion/reordering; null entries mean new details.
    detail_ids: list[int | None] | None = None
    filename: str | None = Field(default=None, max_length=512)
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    result: DocumentExtraction | TemplateExtraction
    # 用户选择忽略的校验问题下标（对应本次评估的 issues 列表）
    ignored_issue_indices: list[Annotated[int, Field(ge=0, strict=True)]] = Field(
        default_factory=list, max_length=100
    )
