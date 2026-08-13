import argparse

from sqlalchemy.orm import Session

from document_pipeline_api.config import Settings
from document_pipeline_api.db import build_engine
from document_pipeline_api.services.rule_audit import audit_rule_engine


def main() -> None:
    parser = argparse.ArgumentParser(prog="document-pipeline-rule-audit")
    parser.add_argument("--fail-on-difference", action="store_true")
    arguments = parser.parse_args()
    engine = build_engine(Settings.local().database_url)
    try:
        with Session(engine, autoflush=False) as session:
            report = audit_rule_engine(session)
            print(report.model_dump_json(indent=2))
    finally:
        engine.dispose()
    if arguments.fail_on_difference and (report.changed or report.failed):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
