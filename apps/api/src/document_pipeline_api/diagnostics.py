from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import tempfile
import zipfile

from document_pipeline_api.config import Settings
from document_pipeline_api.model_providers.base import ModelServiceError
from document_pipeline_api.model_providers.factory import build_model_provider
from document_pipeline_api.version import __version__


MINIMUM_FREE_BYTES = 256 * 1024 * 1024
RECOMMENDED_FREE_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class DiagnosticCheck:
    code: str
    status: str
    message: str
    next_step: str | None = None


@dataclass(frozen=True)
class DiagnosticReport:
    ready: bool
    checks: list[DiagnosticCheck]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "checks": [asdict(check) for check in self.checks],
        }


def create_diagnostic_bundle(report: DiagnosticReport, output: Path) -> Path:
    """Create a deliberately data-free support bundle.

    It does not collect logs, environment variables, database rows, document names/paths,
    model responses, tokens, or Windows user paths.
    """
    destination = output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "diagnostic-report.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "product_version": __version__,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        **report.to_dict(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            archive.writestr(
                "PRIVACY.txt",
                "此诊断包不包含日志、环境变量、数据库、文档名称/路径、文档内容、"
                "模型原始响应、API 密钥、集成令牌或 Windows 用户目录。\n",
            )
        temporary_path.replace(destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination


def run_diagnostics(
    data_dir: Path,
    port: int,
    settings: Settings,
) -> DiagnosticReport:
    checks = [
        _check_data_directory(data_dir),
        _check_disk_space(data_dir),
        _check_port(port),
        _check_model(settings),
    ]
    return DiagnosticReport(
        ready=all(check.status != "error" for check in checks),
        checks=checks,
    )


def _check_data_directory(data_dir: Path) -> DiagnosticCheck:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="diagnostic-",
            dir=data_dir,
            delete=True,
        ) as probe:
            probe.write(b"ok")
            probe.flush()
        return DiagnosticCheck(
            code="data_directory",
            status="ok",
            message="业务数据目录可以安全写入。",
        )
    except OSError:
        return DiagnosticCheck(
            code="data_directory",
            status="error",
            message="业务数据目录无法写入，程序不会启动以避免数据丢失。",
            next_step="请检查当前 Windows 用户对本地应用数据目录的权限。",
        )


def _check_disk_space(data_dir: Path) -> DiagnosticCheck:
    try:
        free = shutil.disk_usage(data_dir).free
    except OSError:
        return DiagnosticCheck(
            code="disk_space",
            status="error",
            message="无法读取业务数据磁盘的剩余空间。",
            next_step="请确认磁盘仍在线且当前用户可以访问。",
        )
    if free < MINIMUM_FREE_BYTES:
        return DiagnosticCheck(
            code="disk_space",
            status="error",
            message="业务数据磁盘剩余空间不足 256 MB。",
            next_step="请至少释放 256 MB；处理大量 PDF 前建议保留 2 GB。",
        )
    if free < RECOMMENDED_FREE_BYTES:
        return DiagnosticCheck(
            code="disk_space",
            status="warning",
            message="业务数据磁盘剩余空间不足 2 GB。",
            next_step="当前可以启动，但批量处理前建议释放更多空间。",
        )
    return DiagnosticCheck(
        code="disk_space",
        status="ok",
        message="业务数据磁盘空间充足。",
    )


def _check_port(port: int) -> DiagnosticCheck:
    if not 1 <= port <= 65535:
        return DiagnosticCheck(
            code="app_port",
            status="error",
            message="应用端口不在有效范围内。",
            next_step="请选择 1 到 65535 之间的端口。",
        )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        return DiagnosticCheck(
            code="app_port",
            status="error",
            message=f"本机端口 {port} 已被占用。",
            next_step="请先关闭占用该端口的程序，或在高级设置中更换端口。",
        )
    return DiagnosticCheck(
        code="app_port",
        status="ok",
        message=f"本机端口 {port} 可以使用。",
    )


def _check_model(settings: Settings) -> DiagnosticCheck:
    provider = build_model_provider(settings, timeout_seconds=2)
    try:
        models = provider.available_models()
    except ModelServiceError:
        return DiagnosticCheck(
            code="model_service",
            status="warning",
            message="暂时无法连接 AI 服务；程序仍可打开，文件会保留。",
            next_step="启动 LM Studio/Ollama 并加载视觉模型后，在设置页重新检查。",
        )
    finally:
        provider.close()
    if settings.model_name not in models:
        return DiagnosticCheck(
            code="model_service",
            status="warning",
            message=f"AI 服务已连接，但没有加载配置的模型 {settings.model_name}。",
            next_step="加载该视觉模型，或在设置页激活另一套 AI 服务方案。",
        )
    return DiagnosticCheck(
        code="model_service",
        status="ok",
        message=f"AI 服务和模型 {settings.model_name} 已就绪。",
    )
