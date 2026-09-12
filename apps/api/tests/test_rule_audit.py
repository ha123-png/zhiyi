import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from document_pipeline_api.db import Base, build_engine
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.models import ExtractionRecord, TaskRecord
from document_pipeline_api.schemas.templates import TemplateCreate
from document_pipeline_api.services.rule_audit import audit_rule_engine
from document_pipeline_api.services.templates import create_template
from document_pipeline_api.rule_audit import main as audit_main


def test_rule_audit_uses_exact_template_and_never_writes(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "document-pipeline.db"
    engine = build_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        template = create_template(
            session,
            TemplateCreate.model_validate(
                {
                    "name": "审计模板",
                    "description": "验证历史规则差异",
                    "fields": [
                        {
                            "key": "amount",
                            "label": "金额",
                            "value_type": "number",
                        }
                    ],
                    "deterministic_rules": [
                        {"kind": "required", "field": "header.amount"}
                    ],
                }
            ),
        )
        session.add(
            TaskRecord(
                id="audit-task",
                filename="audit.png",
                content_type="image/png",
                size_bytes=1,
                sha256="audit-digest",
                storage_path=str(tmp_path / "audit.png"),
                template_mode="manual",
                template_id=template.id,
                template_version=template.version,
                status=TaskStatus.NEEDS_REVIEW.value,
            )
        )
        session.flush()
        session.add(
            ExtractionRecord(
                task_id="audit-task",
                document_kind="custom",
                template_id=template.id,
                template_version=template.version,
                model_name="historical-model",
                prompt_version="historical-prompt",
                rule_engine_version="legacy-unversioned",
                elapsed_seconds=1,
                result_json=json.dumps({"header": {"amount": None}, "items": []}),
                validation_json="[]",
            )
        )
        session.commit()

        before = session.get(ExtractionRecord, "audit-task")
        assert before is not None
        before_values = (before.result_json, before.validation_json, before.rule_engine_version)
        row_count = session.scalar(select(func.count(ExtractionRecord.task_id)))

        report = audit_rule_engine(session)

        assert report.scanned == 1
        assert report.changed == 1
        assert report.failed == 0
        assert report.entries[0].template_id == template.id
        assert report.entries[0].template_version == template.version
        assert report.entries[0].stored_engine_version == "legacy-unversioned"
        assert report.entries[0].current_engine_version == "template-v2"
        assert report.entries[0].current_issues[0].field == "header.amount"
        assert not session.dirty
        assert not session.new
        session.rollback()

    with Session(engine) as verification:
        after = verification.get(ExtractionRecord, "audit-task")
        assert after is not None
        assert (after.result_json, after.validation_json, after.rule_engine_version) == before_values
        assert verification.scalar(select(func.count(ExtractionRecord.task_id))) == row_count

    engine.dispose()

    monkeypatch.setenv("DOCUMENT_PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        ["document-pipeline-rule-audit", "--fail-on-difference"],
    )
    with pytest.raises(SystemExit) as exit_info:
        audit_main()
    assert exit_info.value.code == 2
    assert json.loads(capsys.readouterr().out)["changed"] == 1
