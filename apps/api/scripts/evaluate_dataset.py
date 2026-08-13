import argparse
from dataclasses import replace
import json
from pathlib import Path

from document_pipeline_api.config import Settings
from document_pipeline_api.evaluation.quality import (
    QualityReport,
    QualityThresholds,
    compare_quality_reports,
    evaluate_release_gate,
    load_golden,
    run_quality_evaluation,
)
from document_pipeline_api.model_providers import build_model_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="运行不包含业务值的本地模型质量评测")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--fail-on-threshold", action="store_true")
    parser.add_argument("--provider")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument(
        "--sample",
        action="append",
        help="只运行指定文件名；可重复传入，用于先做低资源单样本探针",
    )
    args = parser.parse_args()

    settings = Settings.local()
    settings = replace(
        settings,
        model_provider=args.provider or settings.model_provider,
        model_base_url=args.base_url or settings.model_base_url,
        model_name=args.model or settings.model_name,
    )
    golden = load_golden(args.golden)
    if args.sample:
        missing = sorted(set(args.sample) - golden.samples.keys())
        if missing:
            parser.error(f"黄金集不存在这些样本：{', '.join(missing)}")
        golden = golden.model_copy(
            update={"samples": {name: golden.samples[name] for name in args.sample}}
        )
    provider = build_model_provider(settings)
    try:
        report = run_quality_evaluation(
            provider,
            provider_name=settings.model_provider,
            dataset_dir=args.dataset,
            golden=golden,
        )
    finally:
        provider.close()

    payload: dict[str, object] = report.model_dump(mode="json")
    regression = None
    if args.baseline:
        baseline = QualityReport.model_validate_json(
            args.baseline.read_text(encoding="utf-8")
        )
        regression = compare_quality_reports(baseline, report)
        payload["regression"] = regression.model_dump(mode="json")
    gate = None
    if args.thresholds:
        thresholds = QualityThresholds.model_validate_json(
            args.thresholds.read_text(encoding="utf-8")
        )
        gate = evaluate_release_gate(report, thresholds)
        payload["release_gate"] = gate.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as output_file:
        output_file.write(serialized)
        output_file.write("\n")
    print(serialized)
    if args.fail_on_regression and (
        regression is None or not regression.comparable or regression.regressions
    ):
        raise SystemExit(2)
    if args.fail_on_threshold and (gate is None or not gate.passed):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
