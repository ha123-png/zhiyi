"""集成配置管理接口：HTTP API 读写密钥与独立的 MCP 权限开关。

密钥/开关落盘到 <data_dir>/config/integration.json（本机数据目录）；API 动态读取，
MCP 在每次客户端连接时由 launcher 加载。接口本身只在本机 Web 界面使用，不走集成 Bearer 认证；
密钥只在生成瞬间返回一次明文，状态查询永不返回密钥内容。
"""

from pathlib import Path
import sys
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from document_pipeline_api.services.integration_config import (
    effective_integration_settings,
    generate_token,
    write_integration_config,
)

router = APIRouter(prefix="/integration/settings", tags=["integration-settings"])


def _data_dir(request: Request):
    return request.app.state.settings.storage_dir.parent


class KeyRequest(BaseModel):
    kind: Literal["read", "write"]


class PermissionRequest(BaseModel):
    task_read: bool = False
    template_read: bool = False
    result_read: bool = False
    data_read: bool = False
    task_control: bool = False
    write_enabled: bool = False
    file_access: bool = False
    file_roots: list[str] = Field(default_factory=list)


@router.get("")
def get_settings(request: Request) -> dict[str, object]:
    """当前配置的集成状态；密钥只返回是否已设置，绝不返回明文。"""
    return effective_integration_settings(_data_dir(request))


def _mcp_server_config(data_dir: Path) -> dict[str, object]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve().with_name("ZhiyiCLI.exe")
        return {"command": str(executable), "args": ["mcp"]}
    project_root = Path(__file__).resolve().parents[5]
    return {
        "command": "uv",
        "args": [
            "--cache-dir",
            str(project_root / ".uv-cache"),
            "run",
            "--project",
            str(project_root / "apps" / "api"),
            "python",
            "-m",
            "document_pipeline_api.launcher",
            "mcp",
            "--data-dir",
            str(data_dir.resolve()),
        ],
    }


@router.get("/mcp-config")
def get_mcp_config(request: Request) -> dict[str, object]:
    server = _mcp_server_config(_data_dir(request))
    return {
        "server_name": "zhiyi",
        "server": server,
        "mcp_servers": {"mcpServers": {"zhiyi": server}},
        "runtime": "installed" if getattr(sys, "frozen", False) else "development",
    }


@router.post("/keys")
def create_key(request: Request, body: KeyRequest) -> dict[str, str]:
    """生成一个新密钥并落盘，返回明文一次（之后不可再查看）。"""
    data_dir = _data_dir(request)
    token = generate_token()
    field = "read_token" if body.kind == "read" else "write_token"
    write_integration_config(data_dir, {field: token})
    return {"kind": body.kind, "token": token}


@router.post("/keys/revoke")
def revoke_key(request: Request, body: KeyRequest) -> dict[str, bool]:
    """撤销（清空）指定密钥：API 鉴权立即读取文件并生效。"""
    field = "read_token" if body.kind == "read" else "write_token"
    write_integration_config(_data_dir(request), {field: None})
    return {"revoked": True}


@router.post("/permissions")
def save_permissions(request: Request, body: PermissionRequest) -> dict[str, bool]:
    """保存 MCP 权限开关与文件允许目录；下次 MCP 连接立即采用。"""
    data_dir = _data_dir(request)
    updates: dict[str, object] = {
        "mcp_task_read": body.task_read,
        "mcp_template_read": body.template_read,
        "mcp_result_read": body.result_read,
        "mcp_data_read": body.data_read,
        "mcp_task_control": body.task_control,
        "mcp_write_enabled": body.write_enabled,
        "mcp_file_access": body.file_access,
        "mcp_file_roots": (
            [item for item in body.file_roots if item.strip()] if body.file_access else None
        ),
    }
    write_integration_config(data_dir, updates)
    return {"saved": True}
