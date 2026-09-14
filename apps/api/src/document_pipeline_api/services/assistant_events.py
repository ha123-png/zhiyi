"""Durable incremental output; text delivery does not rewrite the full run log."""

import json
import time
from sqlalchemy import select, func
from document_pipeline_api.models.assistant import (
    AssistantEvent,
    AssistantMessage,
    AssistantRun,
    now,
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def append_text(parts, event):
    index, offset = event.get("part_index", 0), event.get("offset", 0)
    if index > len(parts):
        return  # A later structural snapshot supplies the missing tool boundary.
    if index == len(parts):
        parts.append({"type": "text", "text": ""})
    part = parts[index]
    if part.get("type") != "text":
        return
    text = part["text"]
    if offset <= len(text):
        part["text"] += event.get("text", "")[max(0, len(text) - offset) :]


def materialized_parts(session, run, parts):
    """Recover text committed since the latest full message checkpoint."""
    if not run or not run.stream_version:
        return parts
    for event in session.scalars(
        select(AssistantEvent)
        .where(AssistantEvent.run_id == run.id, AssistantEvent.sequence > run.snapshot_sequence)
        .order_by(AssistantEvent.sequence)
    ):
        body = json.loads(event.payload_json)
        if body.get("type") == "text.delta":
            append_text(parts, body)
    return parts


def recover_interrupted_runs(session):
    for run in session.scalars(
        select(AssistantRun).where(AssistantRun.status.in_(["running", "waiting", "cancelling"]))
    ):
        message = session.get(AssistantMessage, run.message_id)
        if message:
            message.parts_json = encode(
                materialized_parts(session, run, json.loads(message.parts_json))
            )
        if run.stream_version:
            run.snapshot_sequence = (
                session.scalar(
                    select(func.max(AssistantEvent.sequence)).where(AssistantEvent.run_id == run.id)
                )
                or 0
            )
        run.status = "interrupted"
        run.error = "应用已重启；已保存生成内容，请重新提问。"
    session.commit()


class EventWriter:
    def __init__(self, factory, run_id, parts, usage, cancel):
        self.factory, self.run_id, self.parts, self.usage, self.cancel = (
            factory,
            run_id,
            parts,
            usage,
            cancel,
        )
        self.pending = []
        self.sequence = 0
        self.last_checkpoint = 0.0

    def emit(self, kind, **data):
        self.sequence += 1
        if kind == "text.delta":
            data.update(
                part_index=len(self.parts) - 1,
                offset=len(self.parts[-1]["text"]) - len(data["text"]),
            )
        self.pending.append(
            AssistantEvent(
                run_id=self.run_id,
                sequence=self.sequence,
                payload_json=encode({"type": kind, **data}),
            )
        )
        clock = time.monotonic()
        with self.factory() as session:
            run = session.get(AssistantRun, self.run_id)
            if not run:
                self.cancel.set()
                raise InterruptedError("对话已删除。")
            session.add_all(self.pending)
            run.updated_at = now()
            if kind != "text.delta" or clock - self.last_checkpoint >= 0.75:
                message = session.get(AssistantMessage, run.message_id)
                message.parts_json = encode(self.parts)
                run.snapshot_sequence = self.sequence
                run.usage_json = encode(self.usage)
                self.last_checkpoint = clock
            session.commit()
        self.pending.clear()
