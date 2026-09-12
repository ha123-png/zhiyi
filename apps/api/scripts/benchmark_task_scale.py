"""ISSUE-067 压测：任务历史分页与统计接口在 1 万任务 / 10 万事实行下的响应预算验证。

用法（在 apps/api 目录下）：
    uv run --cache-dir .uv-cache python scripts/benchmark_task_scale.py

行为：
- 在临时 SQLite 库中生成 10000 个任务、少量提取快照，以及真实 data_rows 10 万行。
- 测量任务/数据表首页、非空末页、搜索、统计，以及 CSV/JSON 导出与 1 万行导入。
- 结果打印并写入本地 `.local/test-runs/performance-report-issue-067.json`。

验收标准（ISSUE-067）：目标 Windows 硬件上首屏与常用翻页在 2 秒内有反馈；
前后端内存有上限，不一次加载全部历史（前端每页只拉 10 条，本脚本验证服务端响应）。
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import tempfile
import time
from datetime import timedelta
from pathlib import Path

TASK_COUNT = 10_000
SNAPSHOT_TASK_COUNT = 5_000  # 带提取快照的任务数
ITEMS_PER_SNAPSHOT = 20       # 每个快照 20 行明细 → 合计 10 万事实行
FACT_ROW_COUNT = 100_000
INSERT_BATCH = 500
MEDIAN_REPEAT = 5


def median_of_ms(fn, repeat: int = MEDIAN_REPEAT) -> float:
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    os.environ["PYTHONPATH"] = str(source_root)

    from fastapi.testclient import TestClient

    from document_pipeline_api.config import Settings
    from document_pipeline_api.main import create_app
    from sqlalchemy import insert

    from document_pipeline_api.models import (
        DataRowRecord,
        DataTableRecord,
        ExtractionRecord,
        TaskRecord,
    )
    from document_pipeline_api.models.task import utc_now

    temporary_dir = Path(tempfile.mkdtemp(prefix="issue067-bench-"))
    settings = Settings(
        database_url=f"sqlite:///{temporary_dir / 'bench.db'}",
        storage_dir=temporary_dir / "uploads",
        queue_enabled=False,
    )

    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as session:
            print(f"生成 {TASK_COUNT} 个任务…")
            base = utc_now() - timedelta(days=180)
            for batch_start in range(0, TASK_COUNT, INSERT_BATCH):
                batch_end = min(batch_start + INSERT_BATCH, TASK_COUNT)
                session.add_all(
                    [
                        TaskRecord(
                            id=f"bench-{index}",
                            filename=f"发票-{index}.png",
                            content_type="image/png",
                            size_bytes=1024,
                            page_count=1,
                            sha256=f"bench-digest-{index}",
                            storage_path=f"bench-{index}.png",
                            template_mode="smart",
                            status="completed" if index % 100 != 0 else "failed",
                            created_at=base + timedelta(minutes=index),
                            updated_at=base + timedelta(minutes=index),
                        )
                        for index in range(batch_start, batch_end)
                    ]
                )
                session.commit()

            print(f"生成 {SNAPSHOT_TASK_COUNT * ITEMS_PER_SNAPSHOT} 条提取快照明细…")
            for batch_start in range(0, SNAPSHOT_TASK_COUNT, INSERT_BATCH):
                batch_end = min(batch_start + INSERT_BATCH, SNAPSHOT_TASK_COUNT)
                session.add_all(
                    [
                        ExtractionRecord(
                            task_id=f"bench-{index}",
                            document_kind="invoice",
                            template_id="builtin-invoice",
                            template_version=1,
                            model_name="bench-model",
                            prompt_version="bench-v1",
                            elapsed_seconds=2.0,
                            result_json=json.dumps(
                                {
                                    "items": [
                                        {
                                            "name": f"商品 {item}",
                                            "quantity": item,
                                            "amount": 10.0 * item,
                                        }
                                        for item in range(ITEMS_PER_SNAPSHOT)
                                    ]
                                }
                            ),
                            validation_json="[]",
                        )
                        for index in range(batch_start, batch_end)
                    ]
                )
                session.commit()
            print("数据生成完成。\n")

            session.add(
                DataTableRecord(
                    id="bench-table",
                    name="十万行事实表",
                    template_key="bench-table",
                    template_version="1",
                    document_kind="custom",
                    columns_json=json.dumps(
                        [
                            {"key": "name", "label": "名称", "section": "header"},
                            {"key": "amount", "label": "金额", "section": "item"},
                        ],
                        ensure_ascii=False,
                    ),
                )
            )
            session.commit()
            print(f"生成 {FACT_ROW_COUNT} 条真实事实行…")
            for batch_start in range(0, FACT_ROW_COUNT, INSERT_BATCH):
                batch_end = min(batch_start + INSERT_BATCH, FACT_ROW_COUNT)
                session.execute(
                    insert(DataRowRecord),
                    [
                        {
                            "table_id": "bench-table",
                            "task_id": None,
                            "item_index": index + 1,
                            "row_json": json.dumps(
                                {"name": f"事实-{index:06d}", "amount": index},
                                ensure_ascii=False,
                            ),
                            "row_version": 1,
                        }
                        for index in range(batch_start, batch_end)
                    ],
                )
                session.commit()
            print("真实事实行生成完成。\n")

        def run_checks() -> dict[str, object]:
            checks = {}
            cases = {
                "history_first_page": ("/api/v1/tasks", {"limit": 10, "status": "completed"}),
                "history_last_nonempty_page": (
                    "/api/v1/tasks",
                    {"limit": 10, "status": "completed", "offset": 9890},
                ),
                "history_search": (
                    "/api/v1/tasks",
                    {"limit": 10, "status": "completed", "search": "发票-9"},
                ),
                "history_active_only": ("/api/v1/tasks", {"limit": 100, "active_only": "true"}),
                "tasks_summary": ("/api/v1/tasks/summary", {}),
                "stats_summary": ("/api/v1/stats/summary", {}),
                "stats_trend_30d": ("/api/v1/stats/trend", {"days": 30}),
                "table_first_page": ("/api/v1/tables/bench-table", {"page_size": 100}),
                "table_last_page": (
                    "/api/v1/tables/bench-table",
                    {"page": 1000, "page_size": 100},
                ),
                "table_search": (
                    "/api/v1/tables/bench-table",
                    {"page_size": 100, "search": "事实-099999"},
                ),
            }
            for name, (path, params) in cases.items():
                elapsed_ms = median_of_ms(
                    lambda p=path, q=params: client.get(p, params=q)
                )
                checks[name] = round(elapsed_ms, 1)
                print(f"{name}: {elapsed_ms:.1f} ms")
            for export_format in ("csv", "json"):
                name = f"export_{export_format}_100k"
                elapsed_ms = median_of_ms(
                    lambda fmt=export_format: client.get(
                        f"/api/v1/tables/bench-table/export.{fmt}"
                    ),
                    repeat=1,
                )
                checks[name] = round(elapsed_ms, 1)
                print(f"{name}: {elapsed_ms:.1f} ms")

            import_payload = "名称,金额\n" + "".join(
                f"导入-{index},{index}\n" for index in range(10_000)
            )
            start = time.perf_counter()
            imported = client.post(
                "/api/v1/tables/bench-table/import",
                files={"file": ("benchmark.csv", import_payload.encode("utf-8"), "text/csv")},
            )
            imported.raise_for_status()
            checks["import_csv_10k"] = round((time.perf_counter() - start) * 1000, 1)
            print(f"import_csv_10k: {checks['import_csv_10k']:.1f} ms")
            # 响应体大小（内存上限参考）
            response = client.get("/api/v1/tasks", params={"limit": 10, "status": "completed"})
            checks["history_page_bytes"] = len(response.content)
            checks["history_page_count"] = len(response.json())
            return checks

        checks = run_checks()

    report = {
        "issue": "ISSUE-067",
        "generated_at": utc_now().isoformat(),
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "dataset": {
            "tasks": TASK_COUNT,
            "fact_rows": FACT_ROW_COUNT,
            "snapshot_item_values": SNAPSHOT_TASK_COUNT * ITEMS_PER_SNAPSHOT,
            "import_rows": 10_000,
            "note": "fact_rows 为 data_rows 表真实记录；快照数组仅用于任务 record_count 路径。",
        },
        "endpoints_ms_median": checks,
        "budgets_ms": {"interactive": 2000, "bulk_import_export": 30000},
        "verdict": "通过" if (
            max(
                value
                for key, value in checks.items()
                if key not in {"history_page_bytes", "history_page_count"}
                and not key.startswith("export_")
                and not key.startswith("import_")
            ) < 2000
            and max(checks[key] for key in checks if key.startswith(("export_", "import_"))) < 30000
        ) else "不通过",
    }

    report_path = (
        Path(__file__).resolve().parents[3]
        / ".local"
        / "test-runs"
        / "performance-report-issue-067.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="\n") as report_file:
        report_file.write(json.dumps(report, ensure_ascii=False, indent=2))
        report_file.write("\n")
    print(f"\n报告已写入：{report_path}")
    print(f"结论：{report['verdict']}（预算 {report['budgets_ms']}）")


if __name__ == "__main__":
    main()
