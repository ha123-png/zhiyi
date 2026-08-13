from pathlib import Path

import pytest
from pydantic import ValidationError

from document_pipeline_api.schemas.templates import (
    TemplateMatchDecision,
    TemplateRead,
)
from document_pipeline_api.services.template_processing import (
    build_template_extraction_model,
    build_template_extraction_prompt,
    match_template,
)


class FakeProvider:
    model_name = "fake"

    def __init__(self, result: dict) -> None:
        self.result = result
        self.prompt = ""
        self.result_type = None

    def extract_image(self, image_path, prompt, result_type):
        self.prompt = prompt
        self.result_type = result_type
        return result_type.model_validate(self.result)

    def available_models(self) -> list[str]:
        return [self.model_name]

    def close(self) -> None:
        pass


def test_matching_uses_only_template_name_and_description(tmp_path: Path) -> None:
    template = _template()
    provider = FakeProvider(
        {"outcome": "matched", "template_ids": [template.id]}
    )

    decision = match_template(tmp_path / "document.png", [template], provider)

    assert decision == TemplateMatchDecision(
        outcome="matched",
        template_ids=[template.id],
    )
    assert template.name in provider.prompt
    assert template.description in provider.prompt
    assert "绝不能进入分类提示的秘密字段" not in provider.prompt
    assert "字段内部说明" not in provider.prompt


def test_matching_rejects_unknown_template_id(tmp_path: Path) -> None:
    provider = FakeProvider(
        {"outcome": "matched", "template_ids": ["made-up-template"]}
    )

    with pytest.raises(Exception, match="不存在的模板"):
        match_template(tmp_path / "document.png", [_template()], provider)


def test_dynamic_schema_separates_header_and_repeating_items() -> None:
    template = _template()

    result_type = build_template_extraction_model(template)
    result = result_type.model_validate(
        {
            "header": {"supplier": "甲公司"},
            "items": [{"quantity": 2.5}],
        }
    )

    assert result.model_dump() == {
        "header": {"supplier": "甲公司"},
        "items": [{"quantity": 2.5}],
    }
    schema = result_type.model_json_schema()
    assert "supplier" in str(schema)
    assert "quantity" in str(schema)
    with pytest.raises(ValidationError):
        result_type.model_validate(
            {
                "header": {"supplier": "甲公司", "unexpected": "no"},
                "items": [],
            }
        )


def test_extraction_prompt_contains_fields_only_after_selection() -> None:
    prompt = build_template_extraction_prompt(_template())

    assert "绝不能进入分类提示的秘密字段" in prompt
    assert "每条明细重复" in prompt
    assert "无法判断时留空" in prompt


def _template() -> TemplateRead:
    return TemplateRead.model_validate(
        {
            "id": "custom-delivery",
            "version": 3,
            "is_system": False,
            "builtin_key": None,
            "source_template_id": "builtin-delivery",
            "name": "门店送货单",
            "description": "整理门店收到的送货单",
            "extra_instructions": "无法判断时留空",
            "fields": [
                {
                    "key": "supplier",
                    "label": "绝不能进入分类提示的秘密字段",
                    "section": "header",
                    "example": "甲公司",
                    "instructions": "字段内部说明",
                    "value_type": "text",
                },
                {
                    "key": "quantity",
                    "label": "数量",
                    "section": "item",
                    "example": "2",
                    "instructions": "",
                    "value_type": "number",
                },
            ],
            "validation_rules": [],
            "output_mapping": {},
            "created_at": "2026-07-30T00:00:00Z",
            "updated_at": "2026-07-30T00:00:00Z",
        }
    )
