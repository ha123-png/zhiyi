from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from document_pipeline_api.config import Settings
from document_pipeline_api.main import create_app
from document_pipeline_api.models import TaskRecord
from document_pipeline_api.models.task import utc_now


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
