"""Real-model scale acceptance on an explicitly isolated synthetic workspace."""

from argparse import ArgumentParser
from pathlib import Path
import json
import statistics
import threading
import time

import httpx
from sqlalchemy.orm import Session
from document_pipeline_api.db import build_engine
from document_pipeline_api.models import DataTableRecord, DataRowRecord


from verify_assistant_scale_memory import process_memory as memory


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:18814")
    parser.add_argument("--model", default="qwen3.6-flash")
    parser.add_argument(
        "--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if not root.is_relative_to(Path(__file__).resolve().parents[1] / ".local"):
        parser.error("Only workspace .local synthetic data is allowed")
    engine = build_engine(f"sqlite:///{root / 'document-pipeline.db'}")
    with Session(engine) as session:
        if not session.get(DataTableRecord, "ask-synthetic"):
            raise RuntimeError("Start the isolated verification server first")
        for size in (1000, 10000):
            identifier = f"scale-{size}"
            if session.get(DataTableRecord, identifier):
                continue
            session.add(
                DataTableRecord(
                    id=identifier,
                    name=f"规模验收 · {size} 条",
                    template_key=f"manual:scale-{size}",
                    template_version="1",
                    document_kind="custom",
                    columns_json=json.dumps(
                        [
                            dict(key=key, label=label, section="item", value_type=kind)
                            for key, label, kind in [
                                ("date", "日期", "date"),
                                ("vendor", "供应商", "text"),
                                ("amount", "金额（元）", "number"),
                                ("note", "备注", "text"),
                            ]
                        ],
                        ensure_ascii=False,
                    ),
                )
            )
            session.flush()
            for start in range(0, size, 500):
                session.execute(
                    DataRowRecord.__table__.insert(),
                    [
                        dict(
                            table_id=identifier,
                            item_index=i,
                            row_json=json.dumps(
                                {
                                    "date": f"2026-09-{i % 28 + 1:02d}",
                                    "vendor": f"合成供应商{i % 10 + 1:02d}",
                                    "amount": i + 1,
                                    "note": "SCALE_LONG_NOTE "
                                    + "无关的合成备注。" * 400,
                                },
                                ensure_ascii=False,
                            ),
                        )
                        for i in range(start, min(start + 500, size))
                    ],
                )
            session.commit()
    reports = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        base_url=args.url + "/api/v1", timeout=240, trust_env=False
    ) as client:
        response = client.post(
            "/models/profiles",
            json={
                "name": args.model + " · 规模验收",
                "provider": "openai_compatible" if args.key_file else "lm_studio",
                "base_url": args.base_url,
                "model_name": args.model,
                "context_length": None,
                "temperature": 0.1,
                "reasoning_effort": "none",
                "timeout_seconds": 180,
                **(
                    {
                        "api_key": args.key_file.read_text(
                            encoding="utf-8-sig"
                        ).strip(),
                        "acknowledge_remote_data_transfer": True,
                    }
                    if args.key_file
                    else {}
                ),
            },
        )
        response.raise_for_status()
        profile = response.json()
        client.put(
            "/assistant/settings", json={"profile_id": profile["id"]}
        ).raise_for_status()

        def run(size, question, thread=None):
            before = (
                len(
                    (root / "request-sizes.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
                if (root / "request-sizes.jsonl").exists()
                else 0
            )
            sample = [memory(args.pid)]
            done = threading.Event()

            def measure():
                while not done.wait(0.2):
                    sample.append(memory(args.pid))

            sampler = threading.Thread(target=measure, daemon=True)
            sampler.start()
            started = time.monotonic()
            response = client.post(
                "/assistant/runs",
                json={
                    "text": question,
                    "thread_id": thread,
                    "profile_id": profile["id"],
                    "profile_version": profile["version"],
                    "remote_consent": f"{profile['id']}:{profile['version']}"
                    if profile["is_remote"]
                    else None,
                    "context": {"table_id": f"scale-{size}", "mode": "read"},
                },
            )
            response.raise_for_status()
            ids = response.json()
            print(
                json.dumps(
                    {"started": size, "question": question, "run_id": ids["run_id"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            chunks = []
            with client.stream(
                "GET", f"/assistant/runs/{ids['run_id']}/events"
            ) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("type") == "text.delta":
                            chunks.append(
                                (
                                    time.monotonic() - started,
                                    event.get("part_index"),
                                    len(event.get("text", "")),
                                )
                            )
            done.set()
            sampler.join()
            detail = client.get(f"/assistant/threads/{ids['thread_id']}").json()
            current = next(
                value for value in detail["runs"] if value["id"] == ids["run_id"]
            )
            message = next(
                value
                for value in detail["messages"]
                if value["id"] == current["message_id"]
            )
            tool_ids = {
                part["id"] for part in message["parts"] if part["type"] == "tool"
            }
            tools = [value for value in detail["tools"] if value["id"] in tool_ids]
            gaps = [
                round((b[0] - a[0]) * 1000, 1)
                for a, b in zip(chunks, chunks[1:])
                if a[1] == b[1]
            ]
            sizes = [
                json.loads(line)
                for line in (root / "request-sizes.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[before:]
            ]
            report = {
                "size": size,
                "question": question,
                "thread_id": ids["thread_id"],
                "seconds": round(time.monotonic() - started, 2),
                "run": current,
                "working_set_before_mb": round(sample[0] / 1048576, 1),
                "working_set_peak_mb": round(max(sample) / 1048576, 1),
                "stream": {
                    "chunks": len(chunks),
                    "first_text_seconds": round(chunks[0][0], 2) if chunks else None,
                    "median_gap_ms": statistics.median(gaps) if gaps else None,
                    "max_gap_ms": max(gaps) if gaps else None,
                },
                "requests": sizes,
                "tools": tools,
                "answer": "\n".join(
                    part["text"] for part in message["parts"] if part["type"] == "text"
                ),
            }
            return report

        for size in (1000, 10000):
            report = run(
                size,
                "按供应商汇总这张表的金额（元），同时统计记录数，生成柱线图。使用完整表的数据。",
            )
            analyses = [
                t["result"] for t in report["tools"] if t["result"].get("totals")
            ]
            charts = [t["result"] for t in report["tools"] if t["result"].get("chart")]
            expected = {"sum:amount": size * (size + 1) / 2, "count:*": size}
            report["checks"] = {
                "completed": report["run"]["status"] == "completed",
                "exact_totals": any(
                    all(a["totals"].get(k) == v for k, v in expected.items())
                    for a in analyses
                ),
                "complete_scope": any(
                    a["source"]["row_count"] == size for a in analyses
                ),
                "chart": bool(charts),
                "no_unrequested_note": not any(
                    r["unrequested_long_note"] for r in report["requests"]
                ),
            }
            reports.append(report)
            args.output.write_text(
                json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "size": size,
                        "seconds": report["seconds"],
                        "checks": report["checks"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            follow = run(
                size,
                "只读取金额最大的 3 条，返回日期、供应商和金额，不需要备注，也不用重复上一张图。",
                report["thread_id"],
            )
            reads = [t["result"] for t in follow["tools"] if t["result"].get("rows")]
            follow["checks"] = {
                "completed": follow["run"]["status"] == "completed",
                "largest_three": any(
                    [r["values"].get("amount") for r in a["rows"]]
                    == [size, size - 1, size - 2]
                    for a in reads
                ),
                "only_needed_fields": any(
                    all(
                        set(r["values"]) == {"date", "vendor", "amount"}
                        for r in a["rows"]
                    )
                    for a in reads
                ),
            }
            reports.append(follow)
            args.output.write_text(
                json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "size": size,
                        "seconds": follow["seconds"],
                        "checks": follow["checks"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    engine.dispose()
    if not all(all(report["checks"].values()) for report in reports):
        raise SystemExit(
            "Some real-model acceptance checks failed; inspect the report without changing expected facts."
        )


if __name__ == "__main__":
    main()
