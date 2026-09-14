"""Opt-in real-model confirmation journey, only on the isolated ask-synthetic fixture."""

from argparse import ArgumentParser
import json
from pathlib import Path
import time

import httpx


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--context-length", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {"model": args.model, "context_length": args.context_length, "passed": False, "turns": []}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    with httpx.Client(base_url=args.url + "/api/v1", timeout=30, trust_env=False) as client:
        def req(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def rows():
            table = req("GET", "/tables/ask-synthetic")
            return {row["id"]: row for row in table["rows"]}

        originals = rows()
        if len(originals) != 6 or any("total" not in r["values"] for r in originals.values()):
            raise RuntimeError("Only the isolated six-row ask-synthetic fixture is supported.")
        profile = req("POST", "/models/profiles", json={
            "name": args.model + " · 确认流程真实验收",
            "provider": "openai_compatible" if args.api_key_file else "lm_studio",
            "base_url": args.base_url, "model_name": args.model,
            "context_length": args.context_length, "temperature": 0.1, "timeout_seconds": 180,
            # Exercise the unset/default reasoning path that was broken in acceptance.
            "reasoning_effort": None,
            **({"api_key": args.api_key_file.read_text(encoding="utf-8-sig").strip(),
                "acknowledge_remote_data_transfer": True} if args.api_key_file else {}),
        })
        scope = {"table_ids": ["ask-synthetic"]}
        thread = None

        def run(question, on_preview=None):
            nonlocal thread
            before_ids = set()
            if thread:
                before_ids = {t["id"] for t in req("GET", f"/assistant/threads/{thread}")["tools"]}
            started = time.monotonic()
            identifiers = req("POST", "/assistant/runs", json={
                "text": question, "thread_id": thread, "context": scope,
                "profile_id": profile["id"], "profile_version": profile["version"],
                "remote_consent": f"{profile['id']}:{profile['version']}" if profile["is_remote"] else None,
            })
            thread = identifiers["thread_id"]
            handled = set()
            print(json.dumps({"question": question, "run_id": identifiers["run_id"]}, ensure_ascii=False), flush=True)
            while time.monotonic() - started < 240:
                detail = req("GET", f"/assistant/threads/{thread}")
                fresh = [t for t in detail["tools"] if t["id"] not in before_ids]
                pending = [t for t in fresh if t["status"] == "pending"]
                for tool in pending:
                    if on_preview and tool["id"] not in handled:
                        on_preview(tool)
                        handled.add(tool["id"])
                current = next(r for r in detail["runs"] if r["id"] == identifiers["run_id"])
                if current["status"] not in {"running", "waiting", "cancelling"}:
                    break
                time.sleep(.15)
            else:
                req("POST", f"/assistant/runs/{identifiers['run_id']}/cancel")
                raise TimeoutError("Model did not finish; cancelled isolated run.")
            detail = req("GET", f"/assistant/threads/{thread}")
            evidence["turns"].append({"question": question, "run_id": identifiers["run_id"], "seconds": round(time.monotonic() - started, 2), "detail": detail})
            save()
            assert current["status"] == "completed", current
            fresh = [t for t in detail["tools"] if t["id"] not in before_ids]
            # A fallible model may recover from a rejected tool argument. Record
            # that explicitly; acceptance is based on the completed run, the
            # exact preview and actual database state checked below.
            evidence["turns"][-1]["rejected_tool_requests"] = [
                {"name": t["name"], "error": t["result"].get("error")}
                for t in fresh if t["status"] == "failed"
            ]
            save()
            return detail, fresh

        def approve_increment(tool):
            items = tool["result"]["items"]
            assert all(i["operation"]["kind"] == "increment_rows" and i["operation"]["changes"] == {"total": 1} for i in items), items
            actual = {r["row_id"]: r for i in items for r in i["affected"]}
            assert set(actual) == set(originals)
            for identifier, original in originals.items():
                assert actual[identifier]["before"]["total"] == original["values"]["total"]
                assert actual[identifier]["after"]["total"] == original["values"]["total"] + 1
            first = req("POST", f"/assistant/tools/{tool['id']}/decision", json={"approve": True})
            again = req("POST", f"/assistant/tools/{tool['id']}/decision", json={"approve": True})
            assert first == again

        _, calls = run("读取第一张表的字段结构，只查看，不修改。")
        assert not any(t["name"].startswith("propose_") for t in calls), "A read request must not propose a write."
        assert any(t["name"] == "read_resource" and t["result"].get("columns") for t in calls)
        _, calls = run("是，含税总额这个数值字段，每条记录都增加1。", approve_increment)
        assert len([t for t in calls if t["name"].startswith("propose_")]) == 1
        after = rows()
        assert all(after[i]["values"]["total"] == r["values"]["total"] + 1 for i, r in originals.items())
        detail, calls = run("刚才执行了吗？现在第一条和第二条含税总额各是多少？")
        assert not any(t["name"].startswith("propose_") for t in calls)
        text = "".join(p["text"] for p in detail["messages"][-1]["parts"] if p["type"] == "text").replace(",", "")
        assert all(str(after[i]["values"]["total"]) in text for i in sorted(after)[:2]), text
        _, calls = run("再把第一条记录的含税总额增加2，先给我预览。", lambda tool: req("POST", f"/assistant/tools/{tool['id']}/decision", json={"approve": False}))
        assert len([t for t in calls if t["status"] == "rejected"]) == 1
        assert rows() == after
        detail, calls = run("我刚才取消的修改执行了吗？只说明状态。")
        assert not any(t["name"].startswith("propose_") for t in calls)
        status_text = "".join(p["text"] for p in detail["messages"][-1]["parts"] if p["type"] == "text")
        evidence["status_answer_for_manual_review"] = status_text
        assert rows() == after
        _, calls = run("请给我创建一个带金额校验规则的新模板。")
        assert not any(t["name"].startswith("propose_") or t["name"] == "draft_template" for t in calls)
        assert rows() == after
        evidence["passed"] = True
        evidence["checked"] = ["只读结构", "真实逐行加1", "单一提案", "重复确认幂等", "执行后状态与数值", "取消后没有额外修改", "模板写入移除", "默认reasoning请求"]
        save()
        print("PASS: real-model confirmation journey", flush=True)


if __name__ == "__main__":
    main()
