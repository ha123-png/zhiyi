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


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    extra_instructions: str = Field(default="", max_length=4000)
    fields: list[TemplateField] = Field(min_length=1, max_length=100)
    validation_rules: list[str] = Field(default_factory=list, max_length=50)
    deterministic_rules: list[ValidationRule] = Field(default_factory=list, max_length=50)
    output_mapping: dict[str, str] = Field(default_factory=dict)


class TemplateCreate(TemplateBody):
    pass


class TemplateUpdate(TemplateBody):
    expected_version: int = Field(ge=1)


class TemplateFieldRead(TemplateField):
    key: str


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

    label: str
    section: Literal["header", "item"]
    example: str = ""
    value_type: Literal["text", "number", "date", "boolean"] = "text"


class TemplateDraft(BaseModel):
    """AI 生成模板草稿：只返回结构，不落库；人工确认后走正常保存。"""

    name: str = ""
    description: str = ""
    fields: list[TemplateDraftField]
