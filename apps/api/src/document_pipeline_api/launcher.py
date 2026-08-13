import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

from document_pipeline_api.runtime_paths import default_data_dir
from document_pipeline_api.version import __version__


def configure_runtime_data(data_dir: Path) -> None:
    resolved = data_dir.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    runtime_dir = resolved / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DOCUMENT_PIPELINE_DATA_DIR"] = str(resolved)
    os.environ["DOCUMENT_PIPELINE_QUEUE_DB"] = str(resolved / "queue.db")
    os.environ["DOCUMENT_PIPELINE_WORKER_HEARTBEAT"] = str(
        runtime_dir / "worker-heartbeat.json"
    )
    # 集成配置（读写密钥 + MCP 权限开关）由界面写入 config/integration.json，
    # 启动时加载为环境变量，API/Worker/MCP 子进程一并生效
    from document_pipeline_api.services.integration_config import apply_integration_config

    apply_integration_config(resolved)


def run_api(host: str, port: int) -> None:
    import uvicorn

    from document_pipeline_api.main import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def run_worker() -> None:
    from document_pipeline_api.worker import huey

    # The consumer's default exponential backoff can leave a newly submitted
    # document waiting for seconds after an idle period. This worker is
    # deliberately single-threaded, so poll at a small fixed interval: a
    # completed document then hands off directly to the next one, while an
    # idle worker still sleeps instead of busy-spinning.
    consumer = huey.create_consumer(
        workers=1,
        worker_type="thread",
        initial_delay=0.01,
        max_delay=0.01,
        backoff=1.0,
    )
    consumer.run()


def purge_local_data(data_dir: Path) -> None:
    """Remove this product's local database/files and its referenced API secrets."""
    resolved = data_dir.resolve()
    database = resolved / "document-pipeline.db"
    secret_refs: set[str] = set()
    if database.is_file():
        try:
            with closing(sqlite3.connect(database)) as connection:
                for table, column in (
                    ("model_profile_versions", "secret_ref"),
                    ("tasks", "model_secret_ref"),
                ):
                    try:
                        rows = connection.execute(
                            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
                        ).fetchall()
                        secret_refs.update(
                            value for (value,) in rows if isinstance(value, str) and value.startswith("wincred:")
                        )
                    except sqlite3.Error:
                        continue
        except sqlite3.Error:
            pass
    if os.name == "nt" and secret_refs:
        from document_pipeline_api.model_secrets import create_model_secret_store

        store = create_model_secret_store()
        for secret_ref in secret_refs:
            store.delete(secret_ref)
    if resolved.is_dir():
        shutil.rmtree(resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="document-pipeline")
    parser.add_argument(
        "mode",
        nargs="?",
        default="start",
        choices=(
            "start",
            "stop",
            "api",
            "worker",
            "mcp",
            "backup",
            "restore",
            "diagnose",
            "purge-data",
        ),
        help="内部运行模式；正式版由监督器调用。",
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="API 端口；start 模式未指定时自动避让被占端口并持久化。",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    data_dir = arguments.data_dir or default_data_dir()
    if arguments.mode == "start":
        if (
            Path(sys.executable).stem.lower() == "zhiyi"
            and not arguments.no_browser
        ):
            from document_pipeline_api.desktop import run_desktop

            run_desktop(data_dir, arguments.port)
            return
        from document_pipeline_api.supervisor import run_supervisor

        run_supervisor(data_dir, arguments.port, open_browser=not arguments.no_browser)
        return
    if arguments.mode == "stop":
        from document_pipeline_api.supervisor import request_stop

        if not request_stop(data_dir):
            raise SystemExit("程序未运行或无法安全停止。")
        return
    if arguments.mode == "purge-data":
        purge_local_data(data_dir)
        return
    configure_runtime_data(data_dir)
    if arguments.mode == "diagnose":
        from document_pipeline_api.config import Settings
        from document_pipeline_api.diagnostics import (
            create_diagnostic_bundle,
            run_diagnostics,
        )

        # 诊断不参与自动避让，只检测显式指定或默认端口
        port_to_check = arguments.port if arguments.port is not None else 8765
        report = run_diagnostics(data_dir.resolve(), port_to_check, Settings.local())
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        if arguments.archive is not None:
            output = create_diagnostic_bundle(report, arguments.archive)
            print(f"隐私安全诊断包已创建：{output}")
        if not report.ready:
            raise SystemExit(2)
        return
    if arguments.mode in {"backup", "restore"}:
        if arguments.archive is None:
            raise SystemExit("备份或恢复模式必须通过 --archive 指定文件。")
        from document_pipeline_api.business_backup import (
            create_business_backup,
            restore_business_backup,
        )
        from document_pipeline_api.config import Settings
        from document_pipeline_api.supervisor import SingleInstance

        with SingleInstance(data_dir.resolve() / "runtime" / "instance.lock"):
            if arguments.mode == "backup":
                output = create_business_backup(Settings.local(), arguments.archive)
                print(f"业务备份已创建：{output}")
            else:
                rollback = restore_business_backup(
                    Settings.local(),
                    arguments.archive,
                    data_dir,
                )
                print(f"业务数据已恢复；恢复前数据保留在：{rollback}")
        return
    if arguments.mode == "api":
        run_api(arguments.host, arguments.port if arguments.port is not None else 8765)
        return
    if arguments.mode == "mcp":
        from document_pipeline_api.mcp_server import main as run_mcp

        run_mcp()
        return
    run_worker()


if __name__ == "__main__":
    main()
