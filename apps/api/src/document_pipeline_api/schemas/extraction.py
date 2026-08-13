from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from document_pipeline_api.schemas.templates import TemplateRead


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
    quantity: float | None
    unit_price: float | None
    amount: float | None
    tax_rate: str | None
    tax_amount: float | None
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
    amount_before_tax: float | None
    tax_amount: float | None
    total_amount: float | None
    total_quantity: float | None = None
    remarks: str | None = None
    items: list[LineItem]


TemplateScalar = str | float | bool | None


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


class ExtractionRead(BaseModel):
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


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    result: DocumentExtraction | TemplateExtraction
    # 用户选择忽略的校验问题下标（对应本次评估的 issues 列表）
    ignored_issue_indices: list[int] = Field(default_factory=list, max_length=100)
