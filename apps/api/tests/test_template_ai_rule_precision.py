"""Generated rule suggestions keep the same number contract as saved rules."""

import json
import traceback

import httpx
import pytest

from document_pipeline_api.model_providers.base import ModelResponseError
from document_pipeline_api.model_providers.openai_compatible import OpenAICompatibleProvider
from document_pipeline_api.services.template_ai import (
    AiGeneratedTemplate, _clean_field, _clean_rule_suggestions,
)


def _read_suggestions(rules):
    response = {
        "name": "合成规则", "description": "精度边界", "presentation_mode": "table",
        "fields": [{"label": "数值", "section": "header", "example": "", "value_type": "number"}],
        "rules": rules,
    }
    requests = []

    def complete(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps(response)}, "finish_reason": "stop",
        }]})

    provider = OpenAICompatibleProvider("https://models.invalid/v1", "synthetic",
        client=httpx.Client(transport=httpx.MockTransport(complete)))
    try:
        template = provider.complete_text("Synthetic requirement", AiGeneratedTemplate)
    finally:
        provider.close()
        assert len(requests) == 1
    fields = [_clean_field(field, with_examples=False, index=index)
              for index, field in enumerate(template.fields)]
    return _clean_rule_suggestions(template.rules, fields)


@pytest.mark.parametrize("property", ["minimum", "maximum", "values"])
def test_generated_rule_rejects_integer_that_would_change_before_cleaner(property):
    value = 9007199254740993
    rule = {"kind": "enum" if property == "values" else "range", "field": "数值", "section": "header",
            property: [value] if property == "values" else value}
    with pytest.raises(ModelResponseError) as caught:
        _read_suggestions([rule])
    assert caught.value.code == "model_invalid_response"
    assert str(caught.value) == "模型返回内容不符合字段结构，可以重试或更换模型。"
    visible = "".join(traceback.format_exception(caught.value))
    assert "ValidationError" not in visible
    assert "规则数值精度" not in visible
    assert str(value) not in visible


def test_generated_rule_preserves_exact_numbers_and_enum_text_or_boolean():
    suggestions = _read_suggestions([
        {"kind": "range", "field": "数值", "section": "header", "minimum": 0.1, "maximum": 9007199254740992},
        {"kind": "enum", "field": "数值", "section": "header", "values": ["9007199254740993", True, 1.25]},
    ])
    assert [item.status for item in suggestions] == ["accepted", "accepted"]
    assert suggestions[0].rule.minimum == 0.1
    assert suggestions[0].rule.maximum == 9007199254740992
    assert suggestions[1].rule.values == ["9007199254740993", True, 1.25]
    assert type(suggestions[1].rule.values[0]) is str
    assert type(suggestions[1].rule.values[1]) is bool
