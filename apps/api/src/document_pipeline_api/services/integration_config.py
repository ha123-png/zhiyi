"""集成配置落盘：HTTP API 密钥与独立的 MCP 权限保存在 <data_dir>/config/integration.json。

- 文件只存明文密钥与权限开关，由本机数据目录持有（与数据库同级信任边界），
  前端不保存密钥原文，接口也只在生成瞬间返回一次明文。
- 启动时由 launcher.configure_runtime_data 把文件内容加载为环境变量；
  新建 MCP 连接时重新读取，API 密钥则由接口动态读取文件。
- 文件为权威来源：加载时覆盖同名环境变量，保证「界面撤销/修改后重启一定生效」。
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

CONFIG_FILE_NAME = "integration.json"

# 文件字段 → 环境变量
FIELD_TO_ENV = {
    "read_token": "DOCUMENT_PIPELINE_INTEGRATION_READ_TOKEN",
    "write_token": "DOCUMENT_PIPELINE_INTEGRATION_WRITE_TOKEN",
    "mcp_task_read": "DOCUMENT_PIPELINE_MCP_TASK_READ_ENABLED",
    "mcp_template_read": "DOCUMENT_PIPELINE_MCP_TEMPLATE_READ_ENABLED",
    "mcp_task_control": "DOCUMENT_PIPELINE_MCP_TASK_CONTROL_ENABLED",
    "mcp_write_enabled": "DOCUMENT_PIPELINE_MCP_WRITE_ENABLED",
    "mcp_file_access": "DOCUMENT_PIPELINE_MCP_FILE_ACCESS_ENABLED",
    "mcp_file_roots": "DOCUMENT_PIPELINE_MCP_FILE_ROOTS",
    "mcp_result_read": "DOCUMENT_PIPELINE_MCP_RESULT_READ_ENABLED",
    "mcp_data_read": "DOCUMENT_PIPELINE_MCP_DATA_READ_ENABLED",
}

_ALL_FIELDS = set(FIELD_TO_ENV)


def integration_config_path(data_dir: Path) -> Path:
    return data_dir / "config" / CONFIG_FILE_NAME


def read_integration_config(data_dir: Path) -> dict[str, str]:
    """读取配置文件；文件不存在或损坏时返回空配置。"""
    path = integration_config_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if key not in _ALL_FIELDS:
            continue
        if isinstance(value, bool):
            cleaned[key] = "1" if value else "0"
        elif isinstance(value, list):
            cleaned[key] = os.pathsep.join(str(item) for item in value if str(item).strip())
        elif isinstance(value, str):
            cleaned[key] = value
    return cleaned


def write_integration_config(data_dir: Path, updates: dict[str, object]) -> None:
    """把更新项合并进配置文件并落盘（文件不存在则新建）。

    只接受已知字段；token 用布尔标记删除，其余按值写入。
    """
    path = integration_config_path(data_dir)
    current: dict[str, object] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    if not isinstance(current, dict):
        current = {}
    for key, value in updates.items():
        if key not in _ALL_FIELDS:
            continue
        if value is None or value is False:
            current.pop(key, None)
        elif value is True:
            current[key] = True
        elif isinstance(value, str):
            current[key] = value
        elif isinstance(value, list):
            current[key] = [str(item) for item in value if str(item).strip()]
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")


def apply_integration_config(data_dir: Path) -> None:
    """把配置文件加载为环境变量（文件为权威来源，覆盖已存在的同名环境变量）。

    供 launcher.configure_runtime_data 在启动子进程前调用；
    未配置的字段会清除同名环境变量，防止 MCP 客户端注入环境变量绕过权限文件。
    """
    config = read_integration_config(data_dir)
    for field, env in FIELD_TO_ENV.items():
        if field in config:
            os.environ[env] = config[field]
        else:
            os.environ.pop(env, None)
    os.environ.pop("DOCUMENT_PIPELINE_MCP_WRITE_TOKEN", None)


def generate_token() -> str:
    """生成 ≥32 字符的 URL 安全随机密钥。"""
    return secrets.token_urlsafe(32)


def effective_integration_settings(data_dir: Path) -> dict[str, object]:
    """当前配置的集成状态（只读配置文件，即界面管理与重启后生效的来源）；
    密钥只返回是否已设置，不回明文。"""
    config = read_integration_config(data_dir)

    def enabled(field: str) -> bool:
        return str(config.get(field, "0")).lower() in {"1", "true", "yes"}

    roots = config.get("mcp_file_roots", "")
    return {
        "read_token_set": bool(config.get("read_token")),
        "write_token_set": bool(config.get("write_token")),
        "task_read": enabled("mcp_task_read"),
        "template_read": enabled("mcp_template_read"),
        "result_read": enabled("mcp_result_read"),
        "data_read": enabled("mcp_data_read"),
        "task_control": enabled("mcp_task_control"),
        "write_enabled": enabled("mcp_write_enabled"),
        "file_access": enabled("mcp_file_access"),
        "file_roots": [item for item in str(roots).split(os.pathsep) if item.strip()],
    }
