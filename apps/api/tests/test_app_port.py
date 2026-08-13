from pathlib import Path
import socket

import pytest

from document_pipeline_api.supervisor import (
    _read_saved_port,
    resolve_app_port,
)


def _occupy(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    return sock


def test_resolve_defaults_to_free_default_port(tmp_path: Path) -> None:
    assert resolve_app_port(tmp_path, None) == 8765
    # 默认端口空闲时也持久化，作为"当前选定端口"的记录
    assert _read_saved_port(tmp_path / "runtime" / "app-config.json") == 8765


def test_resolve_skips_occupied_and_persists(tmp_path: Path) -> None:
    occupied = _occupy(8765)
    try:
        port = resolve_app_port(tmp_path, None)
    finally:
        occupied.close()
    assert port > 8765
    assert _read_saved_port(tmp_path / "runtime" / "app-config.json") == port
    # 持久化端口空闲：后续启动稳定复用，不再扫描
    assert resolve_app_port(tmp_path, None) == port


def test_resolve_keeps_saved_port_when_it_becomes_free(tmp_path: Path) -> None:
    occupied = _occupy(8765)
    try:
        port = resolve_app_port(tmp_path, None)
    finally:
        occupied.close()
    assert port != 8765
    assert resolve_app_port(tmp_path, None) == port


def test_explicit_port_wins_without_persisting(tmp_path: Path) -> None:
    assert resolve_app_port(tmp_path, 8977) == 8977
    assert _read_saved_port(tmp_path / "runtime" / "app-config.json") is None


def test_explicit_occupied_port_raises(tmp_path: Path) -> None:
    occupied = _occupy(8988)
    try:
        with pytest.raises(RuntimeError, match="已被占用"):
            resolve_app_port(tmp_path, 8988)
    finally:
        occupied.close()
