"""Run explicitly selected live chat checks against synthetic assistant data only."""

from argparse import ArgumentParser
import json
from pathlib import Path
import time
import httpx

if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-length", type=int, default=16384)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url + "/api/v1", timeout=240) as client:
        tables = client.get("/tables").json()
        if not any(t.get("id") == "ask-synthetic" for t in tables):
            raise RuntimeError("Synthetic fixture missing; refusing live checks.")
        profile_response = client.post(
            "/models/profiles",
            json={
                "name": "问知意本地合成验收",
                "provider": "lm_studio",
                "base_url": "http://127.0.0.1:1234/v1",
                "model_name": args.model,
                "context_length": args.context_length,
                "reasoning_effort": "none",
                "timeout_seconds": 180,
                "temperature": 0.1,
            },
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        reports = []
        for question, context in [
            ("你好，用两句话解释为什么模板有助于整理文件。", {}),
            (
                "请按供应方汇总选中记录的含税总额，画一张条形图，并说明总金额和范围。",
                {
                    "table_id": "ask-synthetic",
                    "table_name": "合成采购账本",
                    "row_ids": [1, 2, 3],
                },
            ),
            (
                "帮我起草一个收支记录模板，包括日期、金额、收支类别三个字段，先让我核对。",
                {},
            ),
        ]:
            started_at = time.monotonic()
            started = client.post(
                "/assistant/runs",
                json={
                    "text": question,
                    "profile_id": profile["id"],
                    "profile_version": profile["version"],
                    "context": context,
                },
            )
            started.raise_for_status()
            ids = started.json()
            print(
                json.dumps(
                    {"started": question, "run_id": ids["run_id"]}, ensure_ascii=False
                ),
                flush=True,
            )
            response = client.get(f"/assistant/runs/{ids['run_id']}/events")
            response.raise_for_status()
            detail = client.get(f"/assistant/threads/{ids['thread_id']}").json()
            reports.append(
                {
                    "question": question,
                    "seconds": round(time.monotonic() - started_at, 2),
                    "detail": detail,
                }
            )
            print(
                json.dumps(
                    {
                        "status": detail["runs"][0]["status"],
                        "error": detail["runs"][0]["error"],
                        "tools": [
                            {"name": t["name"], "status": t["status"]}
                            for t in detail["tools"]
                        ],
                        "seconds": reports[-1]["seconds"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
            )
