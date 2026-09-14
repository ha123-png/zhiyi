"""Real-model, multi-turn acceptance on the explicitly isolated synthetic workspace."""

from argparse import ArgumentParser
import json
from pathlib import Path
import time
import httpx


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8878")
    parser.add_argument("--model", default="zhiyi-ask-rework")
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--context-length", type=int, default=None)
    parser.add_argument(
        "--output", type=Path, default=Path(".local/ask-rework-live-final.json")
    )
    args = parser.parse_args()
    reports = []
    with httpx.Client(
        base_url=args.url + "/api/v1", timeout=240, trust_env=False
    ) as client:
        tables = client.get("/tables").json()
        if not any(t["id"] == "ask-synthetic" for t in tables):
            raise RuntimeError("Only the synthetic acceptance workspace is allowed.")
        response = client.post(
            "/models/profiles",
            json={
                "name": f"{args.model} · 连续对话验收",
                "provider": "openai_compatible" if args.api_key_file else "lm_studio",
                "base_url": args.base_url,
                **(
                    {
                        "api_key": args.api_key_file.read_text(
                            encoding="utf-8-sig"
                        ).strip()
                    }
                    if args.api_key_file
                    else {}
                ),
                "acknowledge_remote_data_transfer": bool(args.api_key_file),
                "model_name": args.model,
                "context_length": args.context_length,
                "temperature": 0.1,
                "reasoning_effort": "none",
                "timeout_seconds": 180,
            },
        )
        response.raise_for_status()
        profile = response.json()

        def run(question, context, thread=None):
            start = time.monotonic()
            response = client.post(
                "/assistant/runs",
                json={
                    "text": question,
                    "context": context,
                    "thread_id": thread,
                    "profile_id": profile["id"],
                    "profile_version": profile["version"],
                    "remote_consent": f"{profile['id']}:{profile['version']}"
                    if profile["is_remote"]
                    else None,
                },
            )
            response.raise_for_status()
            ids = response.json()
            print(
                json.dumps(
                    {"started": question, "id": ids["run_id"]}, ensure_ascii=False
                ),
                flush=True,
            )
            client.get(f"/assistant/runs/{ids['run_id']}/events").raise_for_status()
            detail = client.get(f"/assistant/threads/{ids['thread_id']}").json()
            reports.append(
                {
                    "question": question,
                    "seconds": round(time.monotonic() - start, 2),
                    "detail": detail,
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            current = detail["runs"][0]
            print(
                json.dumps(
                    {
                        "status": current["status"],
                        "error": current["error"],
                        "seconds": reports[-1]["seconds"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if current["status"] != "completed":
                raise RuntimeError(current["error"])
            return detail

        rows_response = client.get("/tables/ask-synthetic")
        rows_response.raise_for_status()
        row_ids = sorted(row["id"] for row in rows_response.json()["rows"])[:3]
        if len(row_ids) != 3:
            raise RuntimeError("Synthetic scope needs three actual records.")
        scope = {"table_id": "ask-synthetic", "row_ids": row_ids}
        catalog = run("你可以看到什么表？", scope)
        calls = catalog["tools"]
        if (
            not calls
            or any(t["name"] != "catalog" for t in calls)
            or not any(
                i["id"] == "ask-synthetic"
                for t in calls
                for i in t["result"].get("items", [])
            )
        ):
            raise RuntimeError("Catalog question took the wrong tool path.")
        detail = run("按供应方汇总选中记录的含税总额，画条形图，并说明总金额。", scope)
        analysis = [
            t["result"]
            for t in detail["tools"]
            if t["name"] == "analyze_data_table" and t["status"] == "completed"
        ]
        if (
            not analysis
            or analysis[-1]["source"]["row_count"] != 3
            or analysis[-1]["totals"].get("sum:total") != 9600
            or analysis[-1]["source"]["request"]["dimensions"] != ["vendor"]
            or {r["vendor"]: r["sum:total"] for r in analysis[-1]["data"]}
            != {"青禾纸业": 4800, "山川印务": 4800}
        ):
            raise RuntimeError(
                "Model did not correctly analyze the selected three rows."
            )
        previous_ids = {t["id"] for t in detail["tools"]}
        detail = run(
            "请用刚才已有的分析快照画环形图，不重新读取表。", scope, detail["id"]
        )
        if any(
            t["name"] == "analyze_data_table"
            for t in detail["tools"]
            if t["id"] not in previous_ids
        ):
            raise RuntimeError("Snapshot reuse unexpectedly re-read the table.")
        if not any(
            t["name"] == "render_chart" and t["status"] == "completed"
            for t in detail["tools"]
        ):
            raise RuntimeError("The model did not render a chart.")
        detail = run("刚才选中的记录一共多少钱？简短回答。", scope, detail["id"])
        detail = run(
            "请把第一条已选记录的含税总额改为 3600，先给预览让我核对。",
            scope,
            detail["id"],
        )
        plans = [
            t
            for t in detail["tools"]
            if t["name"] == "propose_operations" and t["status"] == "pending"
        ]
        if len(plans) != 1:
            raise RuntimeError("Expected exactly one pending operation plan.")
        identifier = plans[0]["id"]
        operations = [i["operation"] for i in plans[0]["result"]["items"]]
        if (
            len(operations) != 1
            or operations[0]["kind"] != "update_rows"
            or operations[0]["row_ids"] != [row_ids[0]]
            or operations[0]["changes"] != {"total": 3600}
        ):
            raise RuntimeError(
                "Unexpected model operation; left pending for inspection."
            )
        client.post(
            f"/assistant/tools/{identifier}/decision", json={"approve": True}
        ).raise_for_status()
        undo = client.post(f"/assistant/tools/{identifier}/undo")
        undo.raise_for_status()
        client.post(
            f"/assistant/tools/{undo.json()['id']}/decision", json={"approve": True}
        ).raise_for_status()
        detail = run(
            "按日期逐月汇总这些记录的含税总额，并用面积图显示趋势。",
            scope,
            detail["id"],
        )
        trends = [
            t["result"]
            for t in detail["tools"]
            if t["name"] == "analyze_data_table" and t["status"] == "completed"
        ]
        if trends[-1]["source"]["request"]["dimensions"] != ["date"] or {
            r["date"]: r["sum:total"] for r in trends[-1]["data"]
        } != {"2026-01": 3200, "2026-02": 4800, "2026-03": 1600}:
            raise RuntimeError("Trend groups or values are incorrect.")
        vision = run(
            "请调用 read_original_page，用视觉读取这个文件第 1 页，告诉我订单编号和总额。不要依据文件名推测。",
            {"task_ids": ["ask-source-image"]},
        )
        text = "\n".join(
            p["text"]
            for m in vision["messages"]
            if m["role"] == "assistant"
            for p in m["parts"]
            if p["type"] == "text"
        )
        if "ZY-2048" not in text or "88" not in text:
            raise RuntimeError("Visual answer did not match the synthetic source.")
        print(
            "PASS: scoped analysis, snapshot reuse, multi-turn memory, preview, apply, undo, trend, original image.",
            flush=True,
        )


if __name__ == "__main__":
    main()
