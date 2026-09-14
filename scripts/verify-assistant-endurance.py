"""Repeated queries, durable streams, cancellation, restart and restore on synthetic scale data."""

from pathlib import Path
from argparse import ArgumentParser
import hashlib
import json
import os
import statistics
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, func
from document_pipeline_api.business_backup import (
    create_business_backup,
    restore_business_backup,
)
from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import AssistantRun, AssistantMessage, DataRowRecord
from document_pipeline_api.services.assistant_analysis import (
    analyze,
    AnalysisRequest,
    Metric,
)
from verify_assistant_scale_memory import process_memory


class StreamFixture:
    def __init__(self, settings, cancel):
        self.cancel = cancel

    def close(self):
        pass

    def stream(self, messages, tools):
        long = "停止测试" in messages[-1]["content"]
        for _ in range(400 if long else 30):
            if self.cancel.is_set():
                raise InterruptedError("已停止生成。")
            yield {"type": "text", "text": "合成流式文字😀。"}
            if long:
                time.sleep(0.003)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    local = Path(__file__).resolve().parents[1] / ".local"
    if not args.source.resolve().is_relative_to(
        local
    ) or not args.output.resolve().is_relative_to(local):
        parser.error("Synthetic .local paths only")
    work = args.output.resolve() / "endurance"
    work.mkdir(parents=True, exist_ok=False)

    def settings(path):
        return Settings(
            database_url=f"sqlite:///{path / 'document-pipeline.db'}",
            storage_dir=path / "uploads",
            queue_enabled=False,
        )

    source = settings(args.source.resolve())
    started = time.monotonic()
    archive = create_business_backup(source, work / "initial.dpbak")
    active = work / "working"
    active.mkdir()
    target = settings(active)
    restore_business_backup(target, archive, active)
    report = {"queries": [], "runs": [], "rss_mb": [], "checks": {}}
    app = create_app(target)
    app.state.conversation_factory = StreamFixture
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            before = {
                i: session.scalar(
                    select(func.count())
                    .select_from(DataRowRecord)
                    .where(DataRowRecord.table_id == f"scale-{i}")
                )
                for i in (1000, 10000)
            }
        for i in range(200):
            at = time.monotonic()
            with app.state.session_factory() as session:
                result = analyze(
                    session,
                    AnalysisRequest(
                        table_id="scale-10000",
                        dimensions=["vendor"],
                        metrics=[Metric(op="sum", field="amount"), Metric(op="count")],
                    ),
                )
                assert result["totals"] == {"sum:amount": 50005000.0, "count:*": 10000}
            if i % 10 == 0:
                page = client.get(
                    f"/api/v1/tables/scale-10000?page={i // 10 + 1}&page_size=50"
                )
                assert page.status_code == 200 and len(page.json()["rows"]) == 50
            report["queries"].append(round(time.monotonic() - at, 4))
            if i % 20 == 0:
                report["rss_mb"].append(round(process_memory(os.getpid()) / 1048576, 1))
                print(
                    json.dumps({"queries": i + 1, "rss_mb": report["rss_mb"][-1]}),
                    flush=True,
                )
        profile = next(
            p
            for p in client.get("/api/v1/models/profiles").json()
            if p["provider"] == "lm_studio"
        )
        thread = None
        for i in range(60):
            cancel = i % 6 == 5
            response = client.post(
                "/api/v1/assistant/runs",
                json={
                    "text": "停止测试" if cancel else f"连续对话{i}",
                    "thread_id": thread,
                    "profile_id": profile["id"],
                    "profile_version": profile["version"],
                    "context": {},
                },
            )
            assert response.status_code == 200, response.text
            ids = response.json()
            thread = ids["thread_id"]
            if cancel:
                time.sleep(0.05)
                assert (
                    client.post(
                        f"/api/v1/assistant/runs/{ids['run_id']}/cancel"
                    ).status_code
                    == 200
                )
            stream = client.get(f"/api/v1/assistant/runs/{ids['run_id']}/events").text
            assert "run.completed" in stream
            value = client.get(f"/api/v1/assistant/threads/{thread}").json()
            run = next(run for run in value["runs"] if run["id"] == ids["run_id"])
            assert run["status"] == ("cancelled" if cancel else "completed"), run
            report["runs"].append(run["status"])
        report["checks"]["no_active_workers"] = (
            not app.state.assistant_active and app.state.active_mutations == 0
        )
        report["checks"]["history_page_bounded"] = (
            len(value["messages"]) == 60 and value["has_more"]
        )
        with app.state.session_factory() as session:
            report["checks"]["facts_unchanged"] = before == {
                i: session.scalar(
                    select(func.count())
                    .select_from(DataRowRecord)
                    .where(DataRowRecord.table_id == f"scale-{i}")
                )
                for i in before
            }
            # Simulate a process ending between an event commit and checkpoint.
            from document_pipeline_api.models.assistant import AssistantEvent

            message_id, run_id = str(uuid4()), str(uuid4())
            session.add(
                AssistantMessage(
                    id=message_id,
                    thread_id=thread,
                    role="assistant",
                    position=120,
                    parts_json="[]",
                )
            )
            session.flush()
            session.add(
                AssistantRun(
                    id=run_id,
                    thread_id=thread,
                    message_id=message_id,
                    profile_id=profile["id"],
                    profile_version=profile["version"],
                    model="fixture",
                    provider="fixture",
                    status="running",
                    stream_version=1,
                )
            )
            session.flush()
            session.add(
                AssistantEvent(
                    run_id=run_id,
                    sequence=1,
                    payload_json=json.dumps(
                        {
                            "type": "text.delta",
                            "part_index": 0,
                            "offset": 0,
                            "text": "重启前已保存😀",
                        }
                    ),
                )
            )
            session.commit()
    app2 = create_app(target)
    with TestClient(app2) as client:
        recovered = client.get(f"/api/v1/assistant/threads/{thread}").json()
        assert recovered["messages"][-1]["parts"][0]["text"] == "重启前已保存😀"
        assert recovered["runs"][0]["status"] == "interrupted"
        report["checks"]["restart_preserves_committed_delta"] = True
        with app2.state.session_factory() as session:
            saved_hash = hashlib.sha256(
                "\n".join(
                    session.scalars(
                        select(AssistantMessage.parts_json).order_by(
                            AssistantMessage.id
                        )
                    )
                ).encode()
            ).hexdigest()
    app2.state.engine.dispose() if hasattr(app2.state, "engine") else None
    final = create_business_backup(target, work / "after.dpbak")
    restored = work / "restored"
    restored.mkdir()
    restore_business_backup(settings(restored), final, restored)
    app3 = create_app(settings(restored))
    with TestClient(app3):
        with app3.state.session_factory() as session:
            restored_hash = hashlib.sha256(
                "\n".join(
                    session.scalars(
                        select(AssistantMessage.parts_json).order_by(
                            AssistantMessage.id
                        )
                    )
                ).encode()
            ).hexdigest()
            report["checks"]["backup_restores_all_messages"] = (
                saved_hash == restored_hash
            )
            report["checks"]["backup_restores_facts"] = (
                session.scalar(
                    select(func.count())
                    .select_from(DataRowRecord)
                    .where(DataRowRecord.table_id == "scale-10000")
                )
                == 10000
            )
    report["seconds"] = round(time.monotonic() - started, 2)
    report["median_query_seconds"] = statistics.median(report["queries"])
    report["max_query_seconds"] = max(report["queries"])
    report["checks"]["memory_stable_after_warmup"] = (
        max(report["rss_mb"][2:]) - min(report["rss_mb"][2:]) < 40
    )
    args.output.mkdir(exist_ok=True)
    (args.output / "endurance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"queries", "runs"}},
            ensure_ascii=False,
        ),
        flush=True,
    )
    assert all(report["checks"].values()), report["checks"]


if __name__ == "__main__":
    main()
