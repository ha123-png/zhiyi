"""Local bindings and frozen per-task intent, without filesystem mutations."""
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.models import TaskRecord, TemplateLocalBindingRecord
from document_pipeline_api.schemas.file_export import LocalExportRead, LocalExportUpdate, TaskExportState
from document_pipeline_api.schemas.templates import TemplateRead
from document_pipeline_api.services.file_copies import suggest_safe_stem
from document_pipeline_api.services.templates import get_template


def read_local_export(session: Session, template_id: str) -> LocalExportRead:
    template = get_template(session, template_id)
    record = session.get(TemplateLocalBindingRecord, template_id)
    if record is None:
        return LocalExportRead()
    return LocalExportRead(
        revision=record.revision, enabled=record.enabled, parent_path=record.parent_path,
        destination=str(Path(record.parent_path) / suggest_safe_stem(template.name)) if record.parent_path else None,
    )


def update_local_export(session: Session, settings: Settings, template_id: str, body: LocalExportUpdate) -> LocalExportRead:
    template = get_template(session, template_id)
    parent = None
    if body.parent_path:
        path = Path(body.parent_path)
        if not path.is_absolute():
            raise HTTPException(422, "请选择本机的绝对文件夹路径。")
        parent = str(path.resolve())
    if body.enabled:
        if parent is None or not Path(parent).is_dir():
            raise HTTPException(422, "目标文件夹不存在或不可访问，请重新选择。")
        destination = (Path(parent) / suggest_safe_stem(template.name)).resolve()
        managed = settings.storage_dir.resolve()
        if destination.is_relative_to(managed) or managed.is_relative_to(destination):
            raise HTTPException(422, "外部副本目标不能与知意内部原件目录重叠。")
    values = dict(template_id=template_id, revision=body.expected_revision + 1, enabled=body.enabled, parent_path=parent)
    # SQLite performs revision comparison and update in one write transaction.
    existing = session.get(TemplateLocalBindingRecord, template_id)
    if existing is None and body.expected_revision != 0:
        raise HTTPException(409, "导出设置已经变化，请刷新后再保存。")
    statement = insert(TemplateLocalBindingRecord).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[TemplateLocalBindingRecord.template_id],
        set_={key: value for key, value in values.items() if key != "template_id"},
        where=TemplateLocalBindingRecord.revision == body.expected_revision,
    )
    result = session.execute(statement)
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "导出设置已经变化，请刷新后再保存。")
    session.commit()
    session.expire_all()
    return read_local_export(session, template_id)


def build_export_snapshot(session: Session, template: TemplateRead) -> str:
    binding = session.get(TemplateLocalBindingRecord, template.id)
    state = TaskExportState()
    if binding is not None:
        state.binding_revision = binding.revision
        if binding.enabled and binding.parent_path:
            state.status = "awaiting_confirmation"
            state.parent_path = binding.parent_path
            state.folder_name = suggest_safe_stem(template.name)
            state.destination = str(Path(binding.parent_path) / state.folder_name)
    return state.model_dump_json()


def snapshot_task_export(session: Session, task: TaskRecord, template: TemplateRead) -> None:
    """Call when a template is determined; retries never re-read mutable binding."""
    if task.export_state_json is None:
        task.export_state_json = build_export_snapshot(session, template)
