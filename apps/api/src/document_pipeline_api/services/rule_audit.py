import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from document_pipeline_api.models.extraction import ExtractionRecord
from document_pipeline_api.models.review import ReviewRevisionRecord
from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    TemplateExtraction,
    ValidationIssue,
)
from document_pipeline_api.services.rule_evaluation import evaluate_rules
from document_pipeline_api.services.processing_input import input_scope_issues


class RuleAuditEntry(BaseModel):
    task_id: str
    template_id: str | None
    template_version: int | None
    result_source: Literal["extraction", "review"]
    review_version: int
    stored_engine_version: str
    current_engine_version: str | None
    changed: bool
    stored_issues: list["RuleIssueSummary"]
    current_issues: list["RuleIssueSummary"]
    error: str | None = None


class RuleIssueSummary(BaseModel):
    code: str
    field: str
    severity: str


class RuleAuditReport(BaseModel):
    scanned: int
    unchanged: int
    changed: int
    failed: int
    entries: list[RuleAuditEntry]


def audit_rule_engine(session: Session) -> RuleAuditReport:
    entries: list[RuleAuditEntry] = []
    extractions = session.scalars(
        select(ExtractionRecord).order_by(ExtractionRecord.task_id)
    ).all()
    for extraction in extractions:
        review = session.scalar(
            select(ReviewRevisionRecord)
            .where(ReviewRevisionRecord.task_id == extraction.task_id)
            .order_by(ReviewRevisionRecord.version.desc())
            .limit(1)
        )
        result_json = review.result_json if review is not None else extraction.result_json
        validation_json = (
            review.validation_json if review is not None else extraction.validation_json
        )
        stored_engine = (
            review.rule_engine_version
            if review is not None
            else extraction.rule_engine_version
        )
        stored_issues: list[RuleIssueSummary] = []
        try:
            document_kind = DocumentKind(extraction.document_kind)
            result_type = (
                TemplateExtraction
                if document_kind is DocumentKind.CUSTOM
                else DocumentExtraction
            )
            result = result_type.model_validate_json(result_json)
            stored_issues = [
                _summarize(ValidationIssue.model_validate(item))
                for item in json.loads(validation_json)
            ]
            evaluation = evaluate_rules(
                session,
                document_kind=document_kind,
                result=result,
                template_id=extraction.template_id,
                template_version=extraction.template_version,
            )
            current_issues = [_summarize(issue) for issue in evaluation.issues + input_scope_issues(extraction.input_scope_json)]
            changed = stored_issues != current_issues
            entries.append(
                RuleAuditEntry(
                    task_id=extraction.task_id,
                    template_id=extraction.template_id,
                    template_version=extraction.template_version,
                    result_source="review" if review is not None else "extraction",
                    review_version=review.version if review is not None else 0,
                    stored_engine_version=stored_engine,
                    current_engine_version=evaluation.engine_version,
                    changed=changed,
                    stored_issues=stored_issues,
                    current_issues=current_issues,
                )
            )
        except (HTTPException, ValueError, TypeError, LookupError, json.JSONDecodeError):
            entries.append(
                RuleAuditEntry(
                    task_id=extraction.task_id,
                    template_id=extraction.template_id,
                    template_version=extraction.template_version,
                    result_source="review" if review is not None else "extraction",
                    review_version=review.version if review is not None else 0,
                    stored_engine_version=stored_engine,
                    current_engine_version=None,
                    changed=True,
                    stored_issues=stored_issues,
                    current_issues=[],
                    error="历史记录无法只读重算，需要人工检查数据和模板版本。",
                )
            )
    failed = sum(entry.error is not None for entry in entries)
    changed = sum(entry.changed and entry.error is None for entry in entries)
    return RuleAuditReport(
        scanned=len(entries),
        unchanged=len(entries) - changed - failed,
        changed=changed,
        failed=failed,
        entries=entries,
    )


def _summarize(issue: ValidationIssue) -> RuleIssueSummary:
    return RuleIssueSummary(
        code=issue.code,
        field=issue.field,
        severity=issue.severity,
    )
