from pathlib import Path
import json
import socket
import zipfile

from document_pipeline_api.config import Settings
from document_pipeline_api.diagnostics import create_diagnostic_bundle, run_diagnostics


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "uploads",
        model_base_url="http://127.0.0.1:1/v1",
        model_timeout_seconds=0.1,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_diagnostics_allow_start_when_only_model_is_offline(tmp_path: Path) -> None:
    report = run_diagnostics(tmp_path / "data", _free_port(), _settings(tmp_path))

    assert report.ready is True
    assert [check.status for check in report.checks[:3]] == ["ok", "ok", "ok"]
    assert report.checks[3].code == "model_service"
    assert report.checks[3].status == "warning"
    assert "程序仍可打开" in report.checks[3].message


def test_diagnostics_block_an_occupied_app_port(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = int(occupied.getsockname()[1])
        report = run_diagnostics(tmp_path / "data", port, _settings(tmp_path))

    assert report.ready is False
    port_check = next(check for check in report.checks if check.code == "app_port")
    assert port_check.status == "error"
    assert str(port) in port_check.message


def test_diagnostic_bundle_excludes_business_content_and_secrets(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    private_marker = "PRIVATE-DOCUMENT-CONTENT-123"
    secret_marker = "secret-token-456"
    data_dir.mkdir()
    (data_dir / "private-invoice.txt").write_text(private_marker, encoding="utf-8")
    (data_dir / "logs").mkdir()
    (data_dir / "logs" / "api.log").write_text(secret_marker, encoding="utf-8")
    report = run_diagnostics(data_dir, _free_port(), _settings(tmp_path))
    bundle = create_diagnostic_bundle(report, tmp_path / "support.zip")

    with zipfile.ZipFile(bundle) as archive:
        assert sorted(archive.namelist()) == ["PRIVACY.txt", "diagnostic-report.json"]
        payload = archive.read("diagnostic-report.json") + archive.read("PRIVACY.txt")
        assert private_marker.encode() not in payload
        assert secret_marker.encode() not in payload
        assert str(tmp_path).encode() not in payload
        parsed = json.loads(archive.read("diagnostic-report.json"))
        assert parsed["schema_version"] == 1
