from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.model_providers.base import ModelUnavailableError
from document_pipeline_api.services.template_ai import (
    AiGeneratedField,
    AiGeneratedRule,
    AiGeneratedTemplate,
    generate_template_draft,
)
from image_test_data import PNG_BYTES


class _FakeProvider:
    def __init__(self, result: AiGeneratedTemplate | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.closed = False
        self.last_system_prompt: str | None = None
        self.used_text_only = False

    @property
    def model_name(self) -> str:
        return "fake"

    def extract_images(self, image_paths, prompt, result_type, *, system_prompt=None):
        self.last_system_prompt = system_prompt
        if self._error is not None:
            raise self._error
        return self._result

    def complete_text(self, prompt, result_type, *, system_prompt=None):
        self.last_system_prompt = system_prompt
        self.used_text_only = True
        if self._error is not None:
            raise self._error
        return self._result

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ai.db'}",
        storage_dir=tmp_path / "uploads",
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _run_generate(
    client: TestClient,
    *,
    fake,
    requirement: str,
    images: int = 1,
    with_rules: bool = False,
):
    with client.app.state.session_factory() as session:
        return generate_template_draft(
            client.app.state.settings,
            session,
            image_paths=[Path(f"sample-{i}.png") for i in range(images)],
            requirement=requirement,
            with_rules=with_rules,
            model_client=fake,
        )


def test_generate_returns_cleaned_fields(client: TestClient) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            name="增值税发票模板",
            description="整理增值税发票的购销双方、金额与明细。",
            fields=[
                AiGeneratedField(label="销售方", section="header", example="某某公司", value_type="text"),
                AiGeneratedField(label="价税合计", section="header", example="372.00", value_type="number"),
                AiGeneratedField(label="商品名称", section="item", example="服务费", value_type="text"),
            ],
        )
    )
    draft = _run_generate(client, fake=fake, requirement="增值税发票")
    assert draft.name == "增值税发票模板"
    assert draft.description == "整理增值税发票的购销双方、金额与明细。"
    assert [field.label for field in draft.fields] == ["销售方", "价税合计", "商品名称"]
    assert draft.fields[1].value_type == "number"
    assert all(field.section in {"header", "item"} for field in draft.fields)
    assert fake.closed is False  # 外部注入的 client 由调用方负责关闭


def test_generate_accepts_safe_rules_and_rejects_invalid_references(
    client: TestClient,
) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            fields=[
                AiGeneratedField(label="合计金额", section="header", value_type="number"),
                AiGeneratedField(label="数量", section="item", value_type="number"),
                AiGeneratedField(label="单价", section="item", value_type="number"),
                AiGeneratedField(label="金额", section="item", value_type="number"),
            ],
            rules=[
                AiGeneratedRule(kind="required", field="合计金额", section="header"),
                AiGeneratedRule(
                    kind="equation",
                    operation="multiply",
                    inputs=["数量", "单价"],
                    input_section="item",
                    result_field="金额",
                    result_section="item",
                ),
                AiGeneratedRule(kind="range", field="不存在", section="header", minimum=0),
                AiGeneratedRule(kind="python", field="合计金额"),
            ],
        )
    )

    draft = _run_generate(
        client,
        fake=fake,
        requirement="送货单",
        with_rules=True,
    )

    assert [item.status for item in draft.rule_suggestions] == [
        "accepted",
        "accepted",
        "rejected",
        "rejected",
    ]
    assert draft.rule_suggestions[0].rule.field == "header.field_1"
    assert draft.rule_suggestions[1].rule.field == "items[].field_4"
    assert draft.rule_suggestions[0].summary == "表头的合计金额不能为空"
    assert "关键字段" in draft.rule_suggestions[0].explanation
    assert draft.rule_suggestions[1].summary == "每条明细：数量 × 单价 = 金额"
    assert "计算关系" in draft.rule_suggestions[1].explanation
    assert "不存在" in draft.rule_suggestions[2].reason
    assert "不会" not in draft.rule_suggestions[2].explanation
    assert "不受支持" in draft.rule_suggestions[3].reason


def test_generate_rejects_cross_section_row_equation_with_readable_reason(
    client: TestClient,
) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            fields=[
                AiGeneratedField(label="平时成绩", section="item", value_type="number"),
                AiGeneratedField(label="期末成绩", section="item", value_type="number"),
                AiGeneratedField(label="总成绩", section="header", value_type="number"),
            ],
            rules=[
                AiGeneratedRule(
                    kind="equation",
                    operation="add",
                    inputs=["平时成绩", "期末成绩"],
                    input_section="item",
                    result_field="总成绩",
                    result_section="header",
                ),
            ],
        )
    )

    draft = _run_generate(client, fake=fake, requirement="学生成绩", with_rules=True)

    suggestion = draft.rule_suggestions[0]
    assert suggestion.status == "rejected"
    assert suggestion.summary == "每条明细：平时成绩 + 期末成绩 = 总成绩"
    assert suggestion.reason == "逐行计算的输入字段和结果字段必须都属于每条明细。"
    assert "阻止" in suggestion.explanation


def test_generate_does_not_return_rules_unless_user_asks(client: TestClient) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            fields=[AiGeneratedField(label="金额", section="header", value_type="number")],
            rules=[AiGeneratedRule(kind="required", field="金额", section="header")],
        )
    )
    draft = _run_generate(client, fake=fake, requirement="金额", with_rules=False)
    assert draft.rule_suggestions == []


def test_generate_sends_fixed_system_prompt(client: TestClient) -> None:
    from document_pipeline_api.services.template_ai import SYSTEM_PROMPT

    fake = _FakeProvider(
        AiGeneratedTemplate(fields=[AiGeneratedField(label="名称", section="header")])
    )
    _run_generate(client, fake=fake, requirement="任意需求")
    assert fake.last_system_prompt == SYSTEM_PROMPT
    # 固定系统提示词与用户输入无关，且明确约束只输出字段结构
    assert "字段结构" in SYSTEM_PROMPT
    assert "不编造" in SYSTEM_PROMPT


def test_generate_corrects_invalid_section_and_type(client: TestClient) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            fields=[
                AiGeneratedField(label="数量", section="banana", value_type="currency"),
                AiGeneratedField(label="日期", section="header", value_type="date"),
            ],
        )
    )
    draft = _run_generate(client, fake=fake, requirement="送货单")
    by_label = {field.label: field for field in draft.fields}
    assert by_label["数量"].section == "header"  # 非法 section 纠正为 header
    assert by_label["数量"].value_type == "text"  # 非法类型纠正为 text
    assert by_label["日期"].value_type == "date"


def test_generate_drops_blank_labels_and_caps_at_20(client: TestClient) -> None:
    fields = [
        AiGeneratedField(label=f"字段{i}", section="header", value_type="text")
        for i in range(1, 22)
    ]
    fields.append(AiGeneratedField(label="   ", section="header"))
    fields.append(AiGeneratedField(label="", section="header"))
    fake = _FakeProvider(AiGeneratedTemplate(fields=fields))
    draft = _run_generate(client, fake=fake, requirement="批量字段")
    assert len(draft.fields) == 20  # 上限截断
    assert all(field.label.strip() for field in draft.fields)


def test_generate_requires_requirement(client: TestClient) -> None:
    with pytest.raises(Exception) as error:
        _run_generate(client, fake=_FakeProvider(AiGeneratedTemplate()), requirement="   ")
    assert error.value.status_code == 422


def test_generate_without_images_uses_text_only(client: TestClient) -> None:
    """不上传样例文件：仅凭需求描述也能生成字段结构（走 complete_text）。"""
    fake = _FakeProvider(
        AiGeneratedTemplate(
            name="送货单",
            description="整理送货单的单号、日期、合计与明细。",
            fields=[
                AiGeneratedField(label="送货单号", section="header", value_type="text"),
                AiGeneratedField(label="合计金额", section="header", value_type="number"),
            ],
        )
    )
    with client.app.state.session_factory() as session:
        draft = generate_template_draft(
            client.app.state.settings,
            session,
            image_paths=[],
            requirement="送货单：单号、日期、合计金额和货品明细",
            model_client=fake,
        )
    assert fake.used_text_only is True
    assert draft.name == "送货单"
    assert draft.description == "整理送货单的单号、日期、合计与明细。"
    assert [field.label for field in draft.fields] == ["送货单号", "合计金额"]


def test_generate_surfaces_model_error_as_502(client: TestClient) -> None:
    fake = _FakeProvider(error=ModelUnavailableError("模型服务不可用"))
    with pytest.raises(Exception) as error:
        _run_generate(client, fake=fake, requirement="发票")
    assert error.value.status_code == 502
    assert "AI 生成失败" in error.value.detail


def test_generate_rejects_when_all_fields_invalid(client: TestClient) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(fields=[AiGeneratedField(label="", section="header")])
    )
    with pytest.raises(Exception) as error:
        _run_generate(client, fake=fake, requirement="发票")
    assert error.value.status_code == 422
    assert "没有生成有效字段" in error.value.detail


def test_api_generate_endpoint_returns_draft(
    client: TestClient,
    monkeypatch,
) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            fields=[
                AiGeneratedField(label="销售方", section="header", value_type="text"),
                AiGeneratedField(label="商品名称", section="item", value_type="text"),
            ],
        )
    )
    monkeypatch.setattr(
        "document_pipeline_api.services.template_ai.build_model_provider",
        lambda settings, timeout_seconds=None: fake,
    )
    response = client.post(
        "/api/v1/templates/generate",
        files={"files": ("sample.png", PNG_BYTES, "image/png")},
        data={"requirement": "这是一张增值税发票"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [field["label"] for field in payload["fields"]] == ["销售方", "商品名称"]


def test_api_generate_rejects_unsupported_file_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/templates/generate",
        files={"files": ("sample.txt", b"hello", "text/plain")},
        data={"requirement": "发票"},
    )
    assert response.status_code == 422
    assert "不支持的样例文件类型" in response.json()["detail"]


def test_api_generate_limits_sample_count_and_size(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'limited-samples.db'}",
        storage_dir=tmp_path / "uploads",
        max_upload_bytes=4,
        max_template_sample_files=1,
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as limited_client:
        too_many = limited_client.post(
            "/api/v1/templates/generate",
            files=[
                ("files", ("one.png", b"one", "image/png")),
                ("files", ("two.png", b"two", "image/png")),
            ],
            data={"requirement": "发票"},
        )
        too_large = limited_client.post(
            "/api/v1/templates/generate",
            files={"files": ("large.png", b"12345", "image/png")},
            data={"requirement": "发票"},
        )

    assert too_many.status_code == 413
    assert "最多上传 1 个" in too_many.json()["detail"]
    assert too_large.status_code == 413
    assert "超过" in too_large.json()["detail"]


def test_api_generate_validates_real_image_type_and_aggregate_size(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'sample-boundaries.db'}",
        storage_dir=tmp_path / "uploads",
        max_upload_bytes=len(PNG_BYTES) + 10,
        max_template_sample_total_bytes=len(PNG_BYTES) + 10,
        queue_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        spoofed = test_client.post(
            "/api/v1/templates/generate",
            files={"files": ("sample.png", b"not-an-image", "image/png")},
            data={"requirement": "发票"},
        )
        aggregate = test_client.post(
            "/api/v1/templates/generate",
            files=[
                ("files", ("one.png", PNG_BYTES, "image/png")),
                ("files", ("two.png", PNG_BYTES, "image/png")),
            ],
            data={"requirement": "发票"},
        )

    assert spoofed.status_code == 422
    assert "损坏" in spoofed.json()["detail"]
    assert aggregate.status_code == 413
    assert "总大小" in aggregate.json()["detail"]


def test_api_generate_without_files_returns_draft(
    client: TestClient,
    monkeypatch,
) -> None:
    """不传文件、只填需求描述：接口也应返回草稿。"""
    fake = _FakeProvider(
        AiGeneratedTemplate(
            name="送货单",
            fields=[AiGeneratedField(label="送货单号", section="header", value_type="text")],
        )
    )
    monkeypatch.setattr(
        "document_pipeline_api.services.template_ai.build_model_provider",
        lambda settings, timeout_seconds=None: fake,
    )
    response = client.post(
        "/api/v1/templates/generate",
        data={"requirement": "送货单：单号、日期、合计金额"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert fake.used_text_only is True
    assert payload["fields"][0]["label"] == "送货单号"


def test_generate_without_examples_strips_examples(client: TestClient) -> None:
    """关闭示例：模型生成的 example 一律清空（服务端兜底，不依赖模型自觉）。"""
    fake = _FakeProvider(
        AiGeneratedTemplate(
            name="发票",
            fields=[
                AiGeneratedField(label="发票号码", section="header", example="12345678", value_type="text"),
                AiGeneratedField(label="价税合计", section="header", example="372.00", value_type="number"),
            ],
        )
    )
    with client.app.state.session_factory() as session:
        draft = generate_template_draft(
            client.app.state.settings,
            session,
            image_paths=[Path("sample.png")],
            requirement="发票",
            with_examples=False,
            model_client=fake,
        )
    assert all(field.example == "" for field in draft.fields)


def test_api_generate_without_examples_returns_empty_examples(
    client: TestClient,
    monkeypatch,
) -> None:
    fake = _FakeProvider(
        AiGeneratedTemplate(
            name="送货单",
            fields=[AiGeneratedField(label="送货单号", section="header", example="SH-001", value_type="text")],
        )
    )
    monkeypatch.setattr(
        "document_pipeline_api.services.template_ai.build_model_provider",
        lambda settings, timeout_seconds=None: fake,
    )
    response = client.post(
        "/api/v1/templates/generate",
        data={"requirement": "送货单", "with_examples": "false"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["fields"][0]["example"] == ""


def test_api_generate_rejects_missing_requirement(client: TestClient) -> None:
    response = client.post(
        "/api/v1/templates/generate",
        files={"files": ("sample.png", b"fake", "image/png")},
        data={"requirement": ""},
    )
    # FastAPI 对空表单字段直接 422（字段缺失/为空），服务层另有"请先填写需求描述"兜底
    assert response.status_code == 422


def test_extract_json_text_strips_chitchat_and_fences() -> None:
    from document_pipeline_api.model_providers.openai_compatible import _extract_json_text

    # 前缀废话（"好的，让我来生成…"）应被剔除，只保留 JSON 对象
    noisy = '好的，让我来生成你的模板：\n{"name": "发票", "fields": []}\n以上完成。'
    assert _extract_json_text(noisy) == '{"name": "发票", "fields": []}'

    # markdown 围栏
    fenced = '```json\n{"fields": []}\n```'
    assert _extract_json_text(fenced) == '{"fields": []}'

    # 围栏 + 前缀废话同时存在
    both = '```json\n好的：\n{"name": "送货单", "fields": []}\n```'
    assert _extract_json_text(both) == '{"name": "送货单", "fields": []}'
