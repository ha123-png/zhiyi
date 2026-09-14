from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from document_pipeline_api.schemas.rules import ValidationRule


class TemplateField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    label: str = Field(min_length=1, max_length=128)
    section: Literal["header", "item"] = "header"
    example: str = Field(default="", max_length=256)
    instructions: str = Field(default="", max_length=512)
    value_type: Literal["text", "number", "date", "boolean"] = "text"


class TemplatePresentation(BaseModel):
    """Portable presentation preferences; references use header.key / item.key."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["table", "card"] = "table"
    title_field: str | None = Field(default=None, max_length=160)
    primary_fields: list[str] = Field(default_factory=list, max_length=3)
    collapsed_fields: list[str] = Field(default_factory=list, max_length=100)


class TemplateBehavior(BaseModel):
    """Versioned product choices, deliberately excluding local filesystem paths."""

    model_config = ConfigDict(extra="forbid")

    presentation: TemplatePresentation = Field(default_factory=TemplatePresentation)
    requires_complete_input: bool = True
    suggest_filename: bool = False
    filename_mode: Literal["ai", "fixed"] = "ai"


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    # Legacy understanding rules had no per-rule length limit. Consolidating them
    # must not truncate an existing template; the HTTP request limit still applies.
    extra_instructions: str = ""
    fields: list[TemplateField] = Field(min_length=1, max_length=100)
    validation_rules: list[str] = Field(default_factory=list, max_length=50)
    deterministic_rules: list[ValidationRule] = Field(default_factory=list, max_length=50)
    output_mapping: dict[str, str] = Field(default_factory=dict)
    behavior: TemplateBehavior = Field(default_factory=TemplateBehavior)


class TemplateCreate(TemplateBody):
    pass


class TemplateUpdate(TemplateBody):
    expected_version: int = Field(ge=1)
    expected_updated_at: datetime | None = None


class TemplateVersionSummary(BaseModel):
    version: int
    name: str
    created_at: datetime
    field_count: int


class TemplateVersionRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_updated_at: datetime | None = None


class TemplateRestorationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_version: int
    to_version: int
    created_at: datetime


class TemplateFieldRead(TemplateField):
    key: str = ""


class TemplateRead(TemplateBody):
    fields: list[TemplateFieldRead]
    id: str
    version: int
    is_system: bool
    is_active: bool = True
    # 智能匹配预选池：只有池内模板才会参与智能匹配候选
    in_smart_pool: bool = True
    builtin_key: str | None
    source_template_id: str | None
    created_at: datetime
    updated_at: datetime


class TemplateMatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["matched", "ambiguous", "none"]
    template_ids: list[str] = Field(max_length=3)


class TemplateDraftField(BaseModel):
    """AI 生成的草稿字段：不含内部 key，保存时由后端生成。"""

    key: str
    label: str
    section: Literal["header", "item"]
    example: str = ""
    value_type: Literal["text", "number", "date", "boolean"] = "text"


class TemplateDraftRuleSuggestion(BaseModel):
    """AI 规则建议：合法规则可直接保存，非法建议保留拒绝原因供用户查看。"""

    status: Literal["accepted", "rejected"]
    summary: str
    explanation: str
    reason: str = ""
    rule: ValidationRule | None = None


class TemplateDraft(BaseModel):
    """AI 生成模板草稿：只返回结构，不落库；人工确认后走正常保存。"""

    name: str = ""
    description: str = ""
    fields: list[TemplateDraftField]
    behavior: TemplateBehavior = Field(default_factory=TemplateBehavior)
    rule_suggestions: list[TemplateDraftRuleSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
