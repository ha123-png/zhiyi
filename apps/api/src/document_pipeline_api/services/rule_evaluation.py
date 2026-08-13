from dataclasses import dataclass

from sqlalchemy.orm import Session

from document_pipeline_api.domain.template_rules import (
    TEMPLATE_RULE_ENGINE_VERSION,
    validate_template_rules,
)
from document_pipeline_api.domain.validation import (
    BUILTIN_RULE_ENGINE_VERSION,
    validate_extraction,
)
from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    TemplateExtraction,
    ValidationIssue,
)
from document_pipeline_api.services.templates import get_template_version


@dataclass(frozen=True)
class RuleEvaluation:
    engine_version: str
    issues: list[ValidationIssue]


def evaluate_rules(
    session: Session,
    *,
    document_kind: DocumentKind,
    result: DocumentExtraction | TemplateExtraction,
    template_id: str | None,
    template_version: int | None,
) -> RuleEvaluation:
    if isinstance(result, DocumentExtraction):
        return RuleEvaluation(
            engine_version=BUILTIN_RULE_ENGINE_VERSION,
            issues=validate_extraction(result, document_kind),
        )
    if template_id is None or template_version is None:
        raise ValueError("自定义结果缺少精确模板版本，不能执行规则。")
    template = get_template_version(session, template_id, template_version)
    return RuleEvaluation(
        engine_version=TEMPLATE_RULE_ENGINE_VERSION,
        issues=validate_template_rules(
            result,
            template.deterministic_rules,
            template.fields,
        ),
    )
