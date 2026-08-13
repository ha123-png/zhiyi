import json
import threading
import time
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.model_secrets import ModelSecretStore
from document_pipeline_api.domain.tasks import TaskStatus
from document_pipeline_api.model_providers import ModelProvider, build_model_provider
from document_pipeline_api.model_providers.base import ModelServiceError
from document_pipeline_api.models.extraction import ExtractionRecord
from document_pipeline_api.models.model_profile import ModelProfileVersionRecord
from document_pipeline_api.models.review import ReviewRevisionRecord
from document_pipeline_api.models.task import TaskRecord
from document_pipeline_api.schemas.extraction import (
    DocumentExtraction,
    DocumentKind,
    ExtractionRead,
    FieldEvidence,
    ReviewUpdate,
    TemplateExtraction,
    ValidationIssue,
)
from document_pipeline_api.services.file_formats import (
    CONVERTIBLE_IMAGE_TYPES,
    RASTER_IMAGE_TYPES,
    TEXT_CONTENT_TYPES,
    extract_docx_images,
    extract_text,
    render_image_frames,
    render_text_pages,
    split_text_pages,
)
from document_pipeline_api.services.pdf_rendering import render_pdf_pages
from document_pipeline_api.services.model_runtime import settings_for_task
from document_pipeline_api.services.system_settings import get_bool_setting
from document_pipeline_api.storage_paths import resolve_task_storage_path
from document_pipeline_api.services.rule_evaluation import evaluate_rules
from document_pipeline_api.services.template_processing import (
    build_template_extraction_model,
    build_template_extraction_prompt,
    match_template,
)
from document_pipeline_api.services.templates import (
    ensure_builtin_templates,
    get_template_version,
    list_templates,
)
from document_pipeline_api.services.task_leases import (
    TaskLeaseLostError,
    acquire_task_lease,
    fail_leased_task,
    renew_task_lease,
    transition_leased_task,
)


_PROCESSABLE_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    *CONVERTIBLE_IMAGE_TYPES,
    *TEXT_CONTENT_TYPES,
}

_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# docx 内嵌图片并入识别输入页的上限
_MAX_DOCX_EMBEDDED_IMAGES = 30

PROMPT_VERSION = "builtin-v2"
EXTRACTION_PROMPTS = {
    DocumentKind.INVOICE: (
        "提取发票的买卖方、发票号码、开票日期、不含税金额、总税额、"
        "价税合计和每条商品或服务明细；明细同时提取税率和税额。"
        "严格按照票面“购买方信息”和“销售方信息”的标签确定角色，"
        "不要根据公司名称或版面左右位置猜测。"
    ),
    DocumentKind.DELIVERY: (
        "提取送货单的买卖方、单号、日期、总额和每条商品明细。"
        "送货单抬头公司是销售/送货方，客户名称是购买/收货方；"
        "没有税额字段时使用 null。"
    ),
}


def _latest_review(
    session: Session,
    task_id: str,
) -> ReviewRevisionRecord | None:
    return session.scalar(
        select(ReviewRevisionRecord)
        .where(ReviewRevisionRecord.task_id == task_id)
        .order_by(ReviewRevisionRecord.version.desc())
        .limit(1)
    )


def _read_extraction(
    session: Session,
    record: ExtractionRecord,
) -> ExtractionRead:
    result_type = (
        TemplateExtraction
        if record.document_kind == DocumentKind.CUSTOM.value
        else DocumentExtraction
    )
    original_result = result_type.model_validate_json(record.result_json)
    review = _latest_review(session, record.task_id)
    current_result = (
        result_type.model_validate_json(review.result_json)
        if review is not None
        else original_result
    )
    task = session.get(TaskRecord, record.task_id)
    page_count = task.page_count if task is not None else 1
    return ExtractionRead(
        task_id=record.task_id,
        document_kind=DocumentKind(record.document_kind),
        template_id=record.template_id,
        template_version=record.template_version,
        template=(
            get_template_version(
                session,
                record.template_id,
                record.template_version,
            )
            if record.template_id is not None and record.template_version is not None
            else None
        ),
        model_name=record.model_name,
        prompt_version=record.prompt_version,
        elapsed_seconds=record.elapsed_seconds,
        review_version=review.version if review is not None else 0,
        original_result=original_result,
        result=current_result,
        validation_issues=[
            ValidationIssue.model_validate(issue)
            for issue in json.loads(
                review.validation_json if review is not None else record.validation_json
            )
        ],
        evidence=_effective_evidence(
            record,
            review,
            current_result,
            page_count=page_count,
        ),
    )


def get_extraction(session: Session, task_id: str) -> ExtractionRead:
    record = session.get(ExtractionRecord, task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="这个任务还没有提取结果。")
    return _read_extraction(session, record)


def save_review(
    session: Session,
    task_id: str,
    update: ReviewUpdate,
) -> ExtractionRead:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if task.status not in {
        TaskStatus.NEEDS_REVIEW.value,
        TaskStatus.COMPLETED.value,
    }:
        raise HTTPException(
            status_code=409,
            detail="只有待确认或已完成任务可以保存修改。",
        )
    extraction = session.get(ExtractionRecord, task_id)
    if extraction is None:
        raise HTTPException(status_code=404, detail="这个任务还没有提取结果。")

    latest = _latest_review(session, task_id)
    current_version = latest.version if latest is not None else 0
    if update.expected_version != current_version:
        raise HTTPException(
            status_code=409,
            detail="结果已在其他位置更新，请刷新后再修改。",
        )
    result_type = (
        TemplateExtraction
        if extraction.document_kind == DocumentKind.CUSTOM.value
        else DocumentExtraction
    )
    current_result = (
        result_type.model_validate_json(latest.result_json)
        if latest is not None
        else result_type.model_validate_json(extraction.result_json)
    )
    if isinstance(update.result, DocumentExtraction):
        saved_result = update.result
    else:
        if extraction.template_id is None or extraction.template_version is None:
            raise HTTPException(status_code=409, detail="这次结果缺少模板版本，不能保存。")
        template = get_template_version(
            session,
            extraction.template_id,
            extraction.template_version,
        )
        dynamic_type = build_template_extraction_model(template)
        try:
            dynamic_result = dynamic_type.model_validate(update.result.model_dump())
        except ValidationError as error:
            raise HTTPException(
                status_code=422,
                detail="提交的字段与模板不符，请刷新后按模板字段重新填写。",
            ) from error
        saved_result = TemplateExtraction.model_validate(dynamic_result.model_dump())
    evaluation = evaluate_rules(
        session,
        document_kind=DocumentKind(extraction.document_kind),
        result=saved_result,
        template_id=extraction.template_id,
        template_version=extraction.template_version,
    )
    issues = evaluation.issues
    # 用户可显式忽略部分校验问题：仍记录（审计语义不变），但不再阻断保存/确认
    ignored_set = set(update.ignored_issue_indices)
    stored_issues = [
        issue.model_copy(update={"ignored": index in ignored_set})
        for index, issue in enumerate(issues)
    ]
    blocking_issues = [issue for issue in stored_issues if not issue.ignored]
    revision = ReviewRevisionRecord(
        task_id=task_id,
        version=current_version + 1,
        result_json=saved_result.model_dump_json(),
        validation_json=json.dumps(
            [issue.model_dump() for issue in stored_issues],
            ensure_ascii=False,
        ),
        rule_engine_version=evaluation.engine_version,
        changes_json=json.dumps(
            _diff_values(current_result.model_dump(), saved_result.model_dump()),
            ensure_ascii=False,
        ),
    )
    session.add(revision)
    session.flush()
    from document_pipeline_api.services.data_tables import (
        confirm_task,
        materialize_task_result,
    )

    if task.status == TaskStatus.COMPLETED.value and blocking_issues:
        # 已完成任务带着未忽略的阻断问题：保存本次草稿与校验问题（含忽略标记），
        # 但不写数据表、不更新确认记录。前端据此展示 error 条目，用户可逐条忽略后再次保存。
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="结果已在其他位置更新，请刷新后再修改。",
            ) from error
        return _read_extraction(session, extraction)

    materialize_task_result(
        session,
        task_id,
        result_json=saved_result.model_dump_json(),
    )
    if task.status == TaskStatus.COMPLETED.value:
        from document_pipeline_api.models import ConfirmedDocumentRecord

        confirmation = session.get(ConfirmedDocumentRecord, task_id)
        if confirmation is not None:
            confirmation.review_version = current_version + 1
            confirmation.result_json = saved_result.model_dump_json()
    elif not blocking_issues:
        confirm_task(
            session,
            task_id,
            expected_review_version=current_version + 1,
        )
        return _read_extraction(session, extraction)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="结果已在其他位置更新，请刷新后再修改。",
        ) from error
    return _read_extraction(session, extraction)


def _diff_values(before, after, path: str = "") -> list[dict[str, object]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, object]] = []
        for key in sorted(before.keys() | after.keys()):
            child_path = f"{path}.{key}" if path else key
            changes.extend(
                _diff_values(before.get(key), after.get(key), child_path)
            )
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(max(len(before), len(after))):
            old_value = before[index] if index < len(before) else None
            new_value = after[index] if index < len(after) else None
            changes.extend(_diff_values(old_value, new_value, f"{path}[{index}]"))
        return changes
    if before == after:
        return []
    return [{"path": path, "before": before, "after": after}]


def _start_lease_keepalive(
    settings: Settings,
    task_id: str,
    token: str,
    lease_for: timedelta,
) -> tuple[threading.Event, threading.Thread]:
    """模型长调用期间周期性续租。

    租约只在模型调用前续一次（余量 model_timeout+60s），而模型客户端的 read 超时
    按“每次读取”计、不是总时长，慢速流式响应可能远超租约余量；此时周期恢复器会把
    健康的处理中任务误判为 worker_interrupted，模型返回后结果被静默丢弃。
    后台线程每 60 秒续一次租约，直到任务到达终态（finally 停止）。
    """
    from document_pipeline_api.db import build_engine

    stop = threading.Event()
    engine = build_engine(settings.database_url)

    def keep_alive() -> None:
        try:
            while not stop.wait(60.0):
                try:
                    with Session(engine) as session:
                        renew_task_lease(
                            session,
                            task_id,
                            token,
                            expected=TaskStatus.PROCESSING,
                            lease_for=lease_for,
                        )
                except Exception:
                    # 续期失败（SQLite 忙/瞬时错误）等下一个周期重试，不让线程提前退出
                    continue
        finally:
            engine.dispose()

    thread = threading.Thread(
        target=keep_alive,
        name=f"lease-keepalive-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return stop, thread


def process_task(
    session: Session,
    settings: Settings,
    task_id: str,
    *,
    client: ModelProvider | None = None,
    secret_store: ModelSecretStore | None = None,
) -> ExtractionRead | None:
    queued_task = session.get(TaskRecord, task_id)
    if queued_task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")
    if queued_task.content_type not in _PROCESSABLE_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="当前文件类型不能交给模型处理。")
    snapshot_error = None
    try:
        task_settings = settings_for_task(settings, queued_task, secret_store)
    except ValueError as error:
        snapshot_error = error
        task_settings = settings
    # 租约基础时长 = 模型超时 + 60s；模型调用期间由后台线程持续续期（见 _start_lease_keepalive），
    # 因此余量只决定恢复器的响应间隔，不再是任务是否被误杀的边界。
    lease_for = timedelta(seconds=task_settings.model_timeout_seconds + 60)
    # 队列暂停兜底：暂停期间 Worker 不启动新任务（任务保持排队，恢复后重新入队处理）
    from document_pipeline_api.services.queue_pause import is_queue_paused

    if is_queue_paused(session):
        raise HTTPException(status_code=409, detail="队列已暂停。")
    lease = acquire_task_lease(
        session,
        task_id,
        lease_for=lease_for,
    )
    if lease is None:
        raise HTTPException(status_code=409, detail="只有等待处理的任务可以开始。")
    # 二次暂停检查：关闭「首次检查 → 获取租约」之间的竞态窗口。暂停事务先设标志、
    # 再 UPDATE 处理中任务，若暂停提交恰好落在两次操作之间，本任务刚拿到的租约
    # 不会被那次 UPDATE 命中，必须在这里把租约退回（转回已暂停），保持队列冻结。
    if is_queue_paused(session):
        transition_leased_task(
            session,
            task_id,
            lease.token,
            expected=TaskStatus.PROCESSING,
            target=TaskStatus.PAUSED,
        )
        raise HTTPException(status_code=409, detail="队列已暂停。")
    if snapshot_error is not None:
        fail_leased_task(
            session,
            task_id,
            lease.token,
            code="model_configuration_invalid",
            message="任务绑定的 AI 配置不完整，请检查模型方案后重试。",
        )
        return None
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="没有找到这个任务。")

    # 仅文本方案的可信拦截：本管线的所有输入（含 txt/docx）都会渲染成图片页喂给模型，
    # 若方案已标记为不支持图片，直接明确失败并给出操作指引，而不是静默产出垃圾结果。
    if (
        task.model_config_version == "profile-v1"
        and task.model_profile_id is not None
        and task.model_profile_version is not None
    ):
        profile_version = session.scalar(
            select(ModelProfileVersionRecord).where(
                ModelProfileVersionRecord.profile_id == task.model_profile_id,
                ModelProfileVersionRecord.version == task.model_profile_version,
            )
        )
        if profile_version is not None and profile_version.multimodal is False:
            fail_leased_task(session, task_id, lease.token, code="model_not_multimodal", message="当前模型不支持图片，请在 LM Studio / Ollama 中加载多模态模型，或更换云端模型。")
            return None

    owns_client = client is None
    model_client = client
    started_at = time.perf_counter()

    try:
        # 模型调用期间持续续租，防止健康任务被恢复器误判中断（见 _start_lease_keepalive）
        keepalive_stop, _keepalive_thread = _start_lease_keepalive(
            settings,
            task_id,
            lease.token,
            lease_for,
        )
        if model_client is None:
            model_client = build_model_provider(
                task_settings,
                timeout_seconds=task_settings.model_timeout_seconds,
            )
        source_path = resolve_task_storage_path(settings.storage_dir, task)
        rendered_dir = settings.storage_dir / "rendered" / task.id
        if task.content_type == "application/pdf":
            image_paths = render_pdf_pages(
                source_path,
                rendered_dir,
                max_pages=settings.max_pdf_pages,
            )
        elif task.content_type in CONVERTIBLE_IMAGE_TYPES or (
            task.content_type in RASTER_IMAGE_TYPES and task.page_count > 1
        ):
            image_paths = render_image_frames(
                source_path,
                rendered_dir,
                task.id,
                max_frames=settings.max_pdf_pages,
                expected_content_type=task.content_type,
                max_total_pixels=settings.max_image_total_pixels,
            )
        elif task.content_type in TEXT_CONTENT_TYPES:
            text = extract_text(
                source_path,
                task.content_type,
                max_uncompressed_bytes=settings.max_import_uncompressed_bytes,
                max_rows=settings.max_import_rows,
                max_columns=settings.max_import_columns,
            )
            text_pages = split_text_pages(text, max_pages=settings.max_pdf_pages)
            image_paths = render_text_pages(text_pages, rendered_dir, task.id)
            # Word 内嵌图片（产品图/签名/含数据截图等）：设置开启时随文字一起交给模型，
            # 关闭时只提取文字、不带内嵌图片（默认关闭，省 token 也避免把无关图片喂给模型）
            if (
                task.content_type == _DOCX_TYPE
                and get_bool_setting(session, "word_include_images", False)
            ):
                image_paths.extend(
                    extract_docx_images(
                        source_path,
                        rendered_dir,
                        task.id,
                        max_uncompressed_bytes=settings.max_import_uncompressed_bytes,
                        max_images=_MAX_DOCX_EMBEDDED_IMAGES,
                        max_total_pixels=settings.max_image_total_pixels,
                    )
                )
        else:
            image_paths = [source_path]
        ensure_builtin_templates(session)
        templates = list_templates(session)
        # 智能匹配只从预选池模板中选（设置页可调整），池内模板语义不重叠时匹配最稳
        smart_pool = [template for template in templates if template.in_smart_pool]
        selected_template = None
        if task.template_id is not None and task.template_version is not None:
            selected_template = get_template_version(
                session,
                task.template_id,
                task.template_version,
            )
        elif task.template_mode in {"invoice", "delivery"}:
            selected_template = next(
                (
                    template
                    for template in templates
                    if template.builtin_key == task.template_mode
                ),
                None,
            )
        else:
            decision = match_template(image_paths[0], smart_pool, model_client)
            if decision.outcome == "matched":
                selected_template = next(
                    template
                    for template in templates
                    if template.id == decision.template_ids[0]
                )
            elif decision.outcome == "ambiguous":
                candidate_lookup = {
                    template.id: template for template in templates
                }
                task.candidate_templates_json = json.dumps(
                    [
                        {
                            "id": template_id,
                            "version": candidate_lookup[template_id].version,
                            "name": candidate_lookup[template_id].name,
                            "description": candidate_lookup[template_id].description,
                        }
                        for template_id in decision.template_ids
                    ]
                )

        if selected_template is None:
            transition_leased_task(
                session,
                task_id,
                lease.token,
                expected=TaskStatus.PROCESSING,
                target=TaskStatus.WAITING_FOR_TEMPLATE,
            )
            return None

        if not renew_task_lease(
            session,
            task_id,
            lease.token,
            expected=TaskStatus.PROCESSING,
            lease_for=lease_for,
            values={
                "template_id": selected_template.id,
                "template_version": selected_template.version,
                "candidate_templates_json": "[]",
            },
        ):
            return None
        if selected_template.builtin_key in {"invoice", "delivery"}:
            document_kind = DocumentKind(selected_template.builtin_key)
            result = _extract_from_pages(
                model_client,
                image_paths,
                EXTRACTION_PROMPTS[document_kind],
                DocumentExtraction,
            )
        else:
            document_kind = DocumentKind.CUSTOM
            dynamic_result_type = build_template_extraction_model(selected_template)
            dynamic_result = _extract_from_pages(
                model_client,
                image_paths,
                build_template_extraction_prompt(selected_template),
                dynamic_result_type,
            )
            result = TemplateExtraction.model_validate(dynamic_result.model_dump())
        if not transition_leased_task(
            session,
            task_id,
            lease.token,
            expected=TaskStatus.PROCESSING,
            target=TaskStatus.VALIDATING,
            # 校验、物化和自动确认同样会遇到 SQLite busy_timeout、磁盘抖动与大明细。
            # 继续沿用本次模型任务租约，而不是突然缩短到固定 60 秒，避免健康任务被
            # 周期恢复器误判为 worker_interrupted。
            lease_for=lease_for,
        ):
            return None
        task = session.get(TaskRecord, task_id)
        if task is None:
            return None
        evaluation = evaluate_rules(
            session,
            document_kind=document_kind,
            result=result,
            template_id=selected_template.id,
            template_version=selected_template.version,
        )
        issues = evaluation.issues
        record = ExtractionRecord(
            task_id=task.id,
            document_kind=document_kind.value,
            template_id=selected_template.id,
            template_version=selected_template.version,
            model_name=model_client.model_name,
            prompt_version=PROMPT_VERSION,
            rule_engine_version=evaluation.engine_version,
            elapsed_seconds=round(time.perf_counter() - started_at, 3),
            result_json=result.model_dump_json(),
            validation_json=json.dumps(
                [issue.model_dump() for issue in issues],
                ensure_ascii=False,
            ),
            evidence_json=json.dumps(
                [
                    evidence.model_dump(mode="json")
                    for evidence in _baseline_evidence(
                        result,
                        page_count=task.page_count,
                    )
                ],
                ensure_ascii=False,
            ),
        )
        session.merge(record)
        session.flush()
        from document_pipeline_api.services.data_tables import (
            confirm_task,
            materialize_task_result,
        )

        # 先保留提取快照，再校验指定表。这样用户选错表后可以直接改选兼容表，
        # 无需再次调用模型；此时不物化数据行，也不进入文件历史完成态。
        if task.target_table_id:
            from document_pipeline_api.models import DataTableRecord

            target_table = session.get(DataTableRecord, task.target_table_id)
            expected_key = (
                selected_template.id
                if selected_template.builtin_key is None
                else selected_template.builtin_key
            )
            expected_version = (
                str(selected_template.version)
                if selected_template.builtin_key is None
                else "builtin-v1"
            )
            if target_table is None or (
                target_table.template_key != expected_key
                or target_table.template_version != expected_version
            ):
                task.candidate_templates_json = json.dumps(
                    [{
                        "id": selected_template.id,
                        "version": selected_template.version,
                        "name": selected_template.name,
                        "description": "提取出字段与指定表不符，请重新选择指定表。",
                    }],
                    ensure_ascii=False,
                )
                if not transition_leased_task(
                    session,
                    task_id,
                    lease.token,
                    expected=TaskStatus.VALIDATING,
                    target=TaskStatus.WAITING_FOR_TEMPLATE,
                ):
                    return None
                return _read_extraction(session, record)

        materialize_task_result(session, task.id)
        if issues:
            if not transition_leased_task(
                session,
                task_id,
                lease.token,
                expected=TaskStatus.VALIDATING,
                target=TaskStatus.NEEDS_REVIEW,
            ):
                return None
        else:
            confirm_task(
                session,
                task.id,
                expected_review_version=0,
                expected_lease_token=lease.token,
            )
        return _read_extraction(session, record)
    except TaskLeaseLostError:
        session.rollback()
        return None
    except Exception as error:
        session.rollback()
        is_match_error = isinstance(error, HTTPException) and error.status_code == 502
        failure_code = (
            "template_match_failed"
            if is_match_error
            else error.code
            if isinstance(error, ModelServiceError)
            else "storage_unavailable"
            if isinstance(error, OSError)
            else "processing_failed"
        )
        failure_message = (
            "智能匹配未能确定这份文件的用途，可重试，或在历史记录里手动选择模板。"
            if is_match_error
            else str(error)
            if isinstance(error, ModelServiceError)
            else "文件存储不可用，请检查磁盘空间和文件权限后重试。"
            if isinstance(error, OSError)
            else "文件处理失败，原文件仍在。请检查模型服务后重试。"
        )
        fail_leased_task(
            session,
            task_id,
            lease.token,
            code=failure_code,
            message=failure_message,
        )
        raise
    finally:
        if owns_client and model_client is not None:
            model_client.close()
        # 停止续租线程；daemon 线程在进程退出时也会自动结束
        keepalive_stop.set()


def _extract_from_pages(
    model_client: ModelProvider,
    image_paths: list[Path],
    prompt: str,
    result_type,
):
    if len(image_paths) == 1:
        return model_client.extract_image(image_paths[0], prompt, result_type)
    return model_client.extract_images(image_paths, prompt, result_type)


def _effective_evidence(
    record: ExtractionRecord,
    review: ReviewRevisionRecord | None,
    result: DocumentExtraction | TemplateExtraction,
    *,
    page_count: int,
) -> list[FieldEvidence]:
    try:
        stored = {
            evidence.field_path: evidence
            for evidence in (
                FieldEvidence.model_validate(item)
                for item in json.loads(record.evidence_json)
            )
        }
    except (TypeError, ValueError):
        stored = {}
    changed_paths = {
        change["path"]
        for change in (json.loads(review.changes_json) if review is not None else [])
        if isinstance(change, dict) and isinstance(change.get("path"), str)
    }
    fallback = {
        evidence.field_path: evidence
        for evidence in _baseline_evidence(result, page_count=page_count)
    }
    evidence_items: list[FieldEvidence] = []
    for field_path, _value in _leaf_values(result.model_dump()):
        if any(_paths_overlap(field_path, changed) for changed in changed_paths):
            evidence_items.append(
                FieldEvidence(
                    field_path=field_path,
                    status="user_edited",
                    source="user",
                    location_verified=False,
                )
            )
            continue
        evidence_items.append(stored.get(field_path) or fallback[field_path])
    return evidence_items


def _baseline_evidence(
    result: DocumentExtraction | TemplateExtraction,
    *,
    page_count: int,
) -> list[FieldEvidence]:
    single_page = page_count == 1
    return [
        FieldEvidence(
            field_path=field_path,
            page_number=1 if single_page else None,
            status="page_only" if single_page else "unavailable",
            source="system",
            location_verified=single_page,
        )
        for field_path, _value in _leaf_values(result.model_dump())
    ]


def _leaf_values(value: object, path: str = "") -> list[tuple[str, object]]:
    if isinstance(value, dict):
        leaves: list[tuple[str, object]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            leaves.extend(_leaf_values(child, child_path))
        return leaves
    if isinstance(value, list):
        leaves = []
        for index, child in enumerate(value):
            leaves.extend(_leaf_values(child, f"{path}[{index}]"))
        return leaves
    return [(path, value)] if value is not None else []


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(f"{right}.")
        or left.startswith(f"{right}[")
        or right.startswith(f"{left}.")
        or right.startswith(f"{left}[")
    )
