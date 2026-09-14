from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.models.task import utc_now
from document_pipeline_api.models import ExtractionRecord
from document_pipeline_api.models.data_table import DataRowRecord, DataTableRecord
from document_pipeline_api.models.dashboard import ModelUsageRecord


def test_dashboard_counts_new_rows_in_old_tables_and_actual_elapsed_samples(tmp_path: Path) -> None:
    now = utc_now()
    old = now - timedelta(days=60)
    with _make_client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            for name, created in [("old", old), ("new", now)]:
                session.add(DataTableRecord(id=name, name=name, template_key=name,
                    template_version="1", document_kind="manual", created_at=created))
            session.flush()
            for table, created in [("old", now), ("old", old), ("new", old), ("new", now)]:
                session.add(DataRowRecord(table_id=table, item_index=0, row_json="{}", created_at=created))
            for name, elapsed in [("first", 10), ("second", 30)]:
                session.add(TaskRecord(id=name, filename=name, content_type="text/plain", size_bytes=1,
                    sha256=name, storage_path=name, status="needs_review"))
                session.flush()
                session.add(ExtractionRecord(task_id=name, document_kind="manual", model_name="synthetic",
                    prompt_version="test", elapsed_seconds=elapsed, result_json="{}", validation_json="{}", created_at=now))
            session.commit()
        summary = client.get("/api/v1/stats/summary", params={"days": 7}).json()
        assert summary["new_rows"] == 2
        assert summary["row_count"] == 4
        assert summary["table_count"] == 2
        assert summary["elapsed_sample_count"] == 2
        assert summary["average_elapsed_seconds"] == 20
        assert summary["processed_count"] == 0  # Review pending is not a completed task.


def _make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stats.db'}",
        storage_dir=tmp_path / "uploads",
    )
    return TestClient(create_app(settings))


def test_stats_trend_aggregates_by_day(tmp_path: Path) -> None:
    """ISSUE-067：/stats/trend 按天聚合任务数，不返回任务明细。"""
    now = utc_now()
    with _make_client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            for index, (task_id, status) in enumerate(
                [
                    ("t1", "completed"),
                    ("t2", "completed"),
                    ("t3", "failed"),
                ]
            ):
                session.add(
                    TaskRecord(
                        id=task_id,
                        filename=f"{task_id}.png",
                        content_type="image/png",
                        size_bytes=1,
                        sha256=f"digest-{task_id}",
                        storage_path=f"{task_id}.png",
                        template_mode="smart",
                        status=status,
                        created_at=now - timedelta(minutes=index),
                    )
                )
            session.commit()

        trend = client.get("/api/v1/stats/trend", params={"days": 7}).json()
        assert len(trend) == 7
        today = trend[-1]
        # 3 个任务都是"现在"创建，应全部落在今天的聚合桶里
        assert today["total"] >= 3
        assert today["completed"] >= 2
        assert today["failed"] >= 1

        # days 参数边界：clamp 到至少 1 天
        single = client.get("/api/v1/stats/trend", params={"days": 0}).json()
        assert len(single) == 1


def test_completion_date_and_received_date_are_distinct(tmp_path):
    now = utc_now()
    with _make_client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            for name, created, completed, status in [
                ("old-completed-today", now - timedelta(days=60), now, "completed"),
                ("new-review", now, None, "needs_review"),
                ("old-completed-unknown", now - timedelta(days=60), None, "completed"),
            ]:
                session.add(TaskRecord(id=name, filename=name, content_type="text/plain", size_bytes=1,
                    sha256=name, storage_path=name, status=status, created_at=created, completed_at=completed))
            session.commit()
        summary = client.get("/api/v1/stats/summary?days=7").json()
        assert summary["current_tasks"] == 3
        assert summary["received_count"] == 1
        assert summary["completed_count"] == 1
        assert summary["review_pending"] == 1
        trend = client.get("/api/v1/stats/trend?days=7").json()
        assert sum(day["received"] for day in trend) == 1
        assert sum(day["completed_on_date"] for day in trend) == 1


def test_all_retained_history_includes_older_than_366_days_across_summary_trend_and_usage(tmp_path):
    now, old = utc_now(), utc_now() - timedelta(days=800)
    with _make_client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            session.add(DataTableRecord(id="history", name="历史", template_key="manual:history", template_version="1", document_kind="manual", columns_json='[{"key":"date","label":"日期","value_type":"date"}]'))
            session.flush()
            for index, moment in enumerate([old, now]):
                session.add(TaskRecord(id=f"history-{index}", filename="synthetic", content_type="text/plain", size_bytes=1, sha256=str(index), storage_path="synthetic", status="completed", created_at=moment, completed_at=moment, template_id="builtin-invoice"))
                session.flush()
                session.add(ExtractionRecord(task_id=f"history-{index}", document_kind="invoice", template_id="builtin-invoice", template_version=1, model_name="synthetic", prompt_version="test", elapsed_seconds=10, result_json="{}", validation_json="{}", created_at=moment))
                session.add(DataRowRecord(table_id="history", item_index=0, row_json='{"date":"2000-01-01"}', created_at=moment))
                session.add(ModelUsageRecord(id=f"usage-{index}", purpose="extraction", model="synthetic", provider="synthetic", status="completed", elapsed_ms=10, usage_json="{}", created_at=moment))
            session.commit()
        current = client.get("/api/v1/stats/summary?days=90").json()
        all_summary = client.get("/api/v1/stats/summary?days=all").json()
        assert all_summary["current_tasks"] == current["current_tasks"] == 2
        assert all_summary["row_count"] == current["row_count"] == 2
        assert current["received_count"] == current["new_rows"] == 1
        assert all_summary["received_count"] == all_summary["completed_count"] == all_summary["new_rows"] == 2
        trend = client.get("/api/v1/stats/trend?days=all").json()
        assert 24 <= len(trend) <= 120
        assert all(point["bucket"] == "month" for point in trend)
        assert sum(point["received"] for point in trend) == sum(point["completed_on_date"] for point in trend) == sum(point["new_rows"] for point in trend) == 2
        assert trend[0]["date"] == old.astimezone().strftime("%Y-%m")
        overview = client.get("/api/v1/stats/overview?days=all").json()
        assert overview["trend_bucket"] == "month"
        assert sum(point["count"] for point in overview["rows_trend"]) == 2
        assert overview["model_usage"]["calls"] == 2
        assert overview["templates"][0]["count"] == 2
        assert client.get("/api/v1/stats/overview?days=90").json()["model_usage"]["calls"] == 1
        preview = client.post("/api/v1/stats/cards/preview?days=all", json={"name":"保留历史", "table_id":"history", "metric":"count", "date_field":"date", "time_range":"dashboard"}).json()
        assert preview["analysis"]["totals"] == {"count:*": 2}


def test_all_extreme_history_has_bounded_year_buckets_without_losing_endpoints(tmp_path):
    with _make_client(tmp_path) as client:
        with client.app.state.session_factory() as session:
            for index, moment in enumerate([datetime(1800, 1, 1, tzinfo=timezone.utc), utc_now(), datetime(2100, 1, 1, tzinfo=timezone.utc)]):
                session.add(TaskRecord(id=f"span-{index}", filename="synthetic", content_type="text/plain", size_bytes=1, sha256=str(index), storage_path="synthetic", status="queued", created_at=moment))
            session.commit()
        trend = client.get("/api/v1/stats/trend?days=all").json()
        assert len(trend) <= 120
        assert {point["bucket"] for point in trend} == {"year"}
        assert trend[0]["interval"] > 1
        assert sum(point["total"] for point in trend) == 3
        assert trend[0]["total"] == trend[-1]["total"] == 1


def test_empty_all_history_does_not_invent_a_date_window(tmp_path):
    with _make_client(tmp_path) as client:
        assert client.get("/api/v1/stats/trend?days=all").json() == []
        overview = client.get("/api/v1/stats/overview?days=all").json()
        assert overview["rows_trend"] == []
        assert overview["model_usage"]["calls"] == 0
