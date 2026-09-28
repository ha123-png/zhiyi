"""Usage: python -m document_pipeline_api.data_baseline_audit DATABASE --output REPORT"""

import argparse
import json
from pathlib import Path

from document_pipeline_api.services.data_baseline_audit import audit_database


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查知意旧数据能否安全迁移为唯一当前数据")
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.database.resolve():
        parser.error("报告路径不能是源数据库。")
    report = audit_database(args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        # Reports are explicit artifacts: refuse to overwrite an earlier report.
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(rendered)
    else:
        print(rendered, end="")
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
