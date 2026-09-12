"""本地模型服务管理抽象层。

统一 LM Studio 与 Ollama 的“服务状态 / 启动服务 / 列模型 / 已加载 / 加载 / 卸载”
接口，供设置页“本地模型”卡片调用。只负责进程与模型管理，不负责任务队列。

实现依据（2026-08 官方 CLI 文档）：
- LM Studio：`lms server start|stop`、`lms ls`（已下载模型）、`lms ps`（已加载）、
  `lms load <model> --context-length N`、`lms unload <model>`；Windows 下 CLI 位于
  `%USERPROFILE%\\.lmstudio\\bin\\lms.exe`，或已加入 PATH。
- Ollama：REST `/api/version`、`/api/tags`（已安装模型）、`/api/ps`（已加载）、
  `/api/generate` 配合 `keep_alive` 预热加载 / `keep_alive: 0` 卸载；`ollama serve`
  在 Windows 上通常是前台进程，自动启动仅作尽力而为。
"""

from collections import deque
import threading

from document_pipeline_api.model_diagnostics import safe_diagnostic

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlparse

import httpx


LM_STUDIO_INSTALL_URL = "https://lmstudio.ai/download?os=windows"


class LocalModelManager:
    provider: str
    base_url: str

    def server_status(self) -> dict:
        raise NotImplementedError

    def start_server(self) -> dict:
        raise NotImplementedError

    def stop_server(self) -> dict:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def loaded_models(self) -> list[str]:
        raise NotImplementedError

    def load_model(
        self,
        model_name: str,
        context_length: int = 8192,
        *,
        identifier: str | None = None,
    ) -> dict:
        raise NotImplementedError

    def unload_model(self, model_name: str) -> dict:
        raise NotImplementedError


class LmStudioManager(LocalModelManager):
    def __init__(self, base_url: str) -> None:
        self.provider = "lm_studio"
        self.base_url = base_url.rstrip("/")
        self._lmstudio_home = _resolve_user_home() / ".lmstudio"
        self._lms = _find_executable(
            "lms",
            self._lmstudio_home / "bin" / "lms.exe",
        )

    def server_status(self) -> dict:
        if self._lms is None:
            return {
                "running": False,
                "installed": False,
                "install_url": LM_STUDIO_INSTALL_URL,
                "message": "没有找到 LM Studio 的服务启动组件。请安装并打开一次 LM Studio；已安装时请检查其命令行工具是否可用。",
            }
        try:
            with httpx.Client(timeout=2, trust_env=False) as client:
                response = client.get(f"{self.base_url}/models")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("invalid models response")
                ids = [item["id"] for item in payload["data"]]
                loaded = ids
                try:
                    detailed = client.get(
                        f"{self.base_url.removesuffix('/v1')}/api/v0/models"
                    )
                    detailed.raise_for_status()
                    loaded = [
                        item["id"]
                        for item in detailed.json().get("data", [])
                        if item.get("state") == "loaded"
                    ]
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    # 兼容没有 v0 状态接口的旧版；启动安全仍由 daemon 能力检查兜底。
                    pass
            return {
                "running": True,
                "installed": True,
                "message": f"服务已运行，当前加载 {len(loaded)} 个模型。",
                "loaded": loaded,
            }
        except httpx.HTTPStatusError as error:
            return {
                "running": False,
                "installed": True,
                "message": f"端口有响应，但不是可用的 LM Studio API（HTTP {error.response.status_code}）。",
            }
        except httpx.RequestError:
            return {"running": False, "installed": True, "message": "服务未运行，可以尝试启动。"}
        except (KeyError, TypeError, ValueError):
            return {
                "running": False,
                "installed": True,
                "message": "配置端口有响应，但返回内容不是 LM Studio API；可能已被其他程序占用。",
            }

    def start_server(self) -> dict:
        if self._lms is None:
            return {"ok": False, "message": "没有找到 LM Studio 的服务启动组件。请先安装并打开一次 LM Studio，再回来刷新。"}
        # 服务已在运行 → 幂等成功
        if self.server_status().get("running"):
            return {"ok": True, "message": "LM Studio 服务已运行。", "detail": "可直接使用。"}
        # 桌面应用会继承后端进程的 Windows 访问令牌。Codex 等受限开发宿主即使把
        # USERPROFILE 指向真实用户目录，也可能只有读取权限；从这里直接 Popen 会让
        # LM Studio 在自己的 test.txt 自检阶段反复弹出 EPERM。先做可回收写探针，
        # 失败时停止自动启动，不放宽用户目录 ACL，也不制造更多弹窗。
        if not self._home_is_writable():
            return {
                "ok": False,
                "message": "当前程序运行在受限环境（如代码编辑器沙箱）中，不能代你启动 LM Studio。",
                "detail": "请从开始菜单或桌面手动打开 LM Studio；服务启动后回到这里点“刷新”。"
                "正常以你自己的账户启动应用时，这里的“启动服务”可以直接拉起 LM Studio。",
            }
        # 先启动 LM Studio 的后台服务模式，再启动 HTTP server。不要直接 Popen 桌面 GUI：
        # GUI 的 Electron 权限自检正是 Windows EPERM 弹窗来源；官方也把 daemon 作为
        # 自动化/无界面运行入口。旧版 CLI 不支持 daemon 时，server start 仍会自行唤醒应用。
        daemon_result = self._run_cli(["daemon", "up"], timeout=30)
        if daemon_result is not None and daemon_result.returncode != 0:
            daemon_error = (daemon_result.stderr or daemon_result.stdout).strip()
            if "unknown command" in daemon_error.lower():
                return {
                    "ok": False,
                    "message": "当前 LM Studio 版本不支持安全的后台启动。",
                    "detail": "请从官方页面升级 LM Studio 后重试。",
                }
            return {
                "ok": False,
                "message": "LM Studio 后台服务启动失败。",
                "detail": _safe_cli_detail(daemon_error, "请先打开一次 LM Studio 完成初始化。"),
            }
        port = urlparse(self.base_url).port or 1234
        server_result = self._run_cli(["server", "start", "--port", str(port)], timeout=30)
        if server_result is not None and server_result.returncode != 0:
            detail = (server_result.stderr or server_result.stdout).strip()
            return {
                "ok": False,
                "message": "LM Studio 本地 API 服务启动失败。",
                "detail": _safe_cli_detail(detail, "请确认端口未被其他程序占用。"),
            }
        # 轮询最多 10 次 × 2 秒探测服务是否真正就绪（应用冷启动可能超过 1 分钟，尤其是
        # 首次从新路径启动需要重新初始化 runtime），总时长约 30-40 秒内返回
        for _ in range(10):
            status = self.server_status()
            if status.get("running"):
                return {"ok": True, "message": "LM Studio 服务已启动。", "detail": status.get("message")}
            time.sleep(2)
        return {
            "ok": False,
            "message": "LM Studio 后台服务已启动，但本地 API 仍未就绪。",
            "detail": "请稍候片刻刷新；若仍失败，请在 LM Studio 中确认本地服务器端口设置。",
        }

    def _run_cli(self, arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [self._lms, *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            # CLI 可能已经把后台动作发出；调用者随后仍会通过 HTTP 状态确认真实结果。
            return None
        except OSError:
            return subprocess.CompletedProcess([self._lms, *arguments], 1, "", "无法执行 lms CLI。")

    def _home_is_writable(self) -> bool:
        """确认当前进程令牌能写 LM Studio 主目录，不依赖环境变量中的用户名。"""
        self._probe_error = None
        probe = self._lmstudio_home / f".document-pipeline-probe-{uuid.uuid4().hex}.tmp"
        try:
            with probe.open("x", encoding="utf-8") as handle:
                handle.write("ok")
            return True
        except OSError as error:
            self._probe_error = repr(error)
            return False
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    def stop_server(self) -> dict:
        if self._lms is None:
            return {"ok": False, "message": "没有找到 lms CLI，无法停止服务。"}
        if not self.server_status().get("running"):
            return {"ok": True, "message": "LM Studio 服务已经停止。"}
        try:
            result = self._run_cli(["server", "stop"], timeout=15)
            if result is None:
                return {"ok": False, "message": "停止服务超时，请稍后刷新确认。"}
            if result.returncode != 0:
                return {
                    "ok": False,
                    "message": "LM Studio 服务停止失败。",
                    "detail": _safe_cli_detail(result.stderr or result.stdout, "请稍后重试。"),
                }
            return {"ok": True, "message": "已停止 LM Studio 服务，可重新启动。"}
        except OSError:
            return {"ok": False, "message": "无法执行 LM Studio 管理命令。"}

    def list_models(self) -> list[str]:
        if self._lms is None:
            return []
        try:
            result = self._run_cli(["ls", "--json"], timeout=30)
            if result is None:
                return []
            if result.returncode != 0:
                return []
            return _parse_cli_models(result.stdout)
        except (subprocess.SubprocessError, OSError):
            return []

    def loaded_models(self) -> list[str]:
        if self._lms is None:
            return []
        try:
            result = self._run_cli(["ps", "--json"], timeout=15)
            if result is None:
                return []
            if result.returncode != 0:
                return []
            return _parse_cli_models(result.stdout, include_identifiers=True)
        except (subprocess.SubprocessError, OSError):
            return []

    def download_model(self, model_reference: str) -> dict:
        if self._lms is None:
            return {"ok": False, "message": "尚未安装 LM Studio，无法下载模型。"}
        result = self._run_cli(
            ["get", model_reference, "--gguf", "--yes"],
            timeout=4 * 60 * 60,
        )
        if result is None:
            return {
                "ok": False,
                "message": "模型下载等待超时。",
                "detail": "再次点击一键配置可由官方下载器继续检查和下载。",
            }
        if result.returncode != 0:
            return {
                "ok": False,
                "message": "LM Studio 模型下载失败。",
                "detail": _safe_cli_detail(result.stderr or result.stdout, "请检查网络和磁盘空间后重试。"),
            }
        return {"ok": True, "message": "LM Studio 已完成模型下载与校验。"}

    def load_model(
        self,
        model_name: str,
        context_length: int = 8192,
        *,
        identifier: str | None = None,
    ) -> dict:
        if self._lms is None:
            return {"ok": False, "message": "没有找到 lms CLI，无法加载模型。"}
        expected_identifier = identifier or model_name
        loaded = self.loaded_models()
        if expected_identifier in loaded or model_name in loaded:
            return {"ok": True, "message": f"{expected_identifier} 已经加载，无需重复加载。"}
        if not self.server_status().get("running"):
            return {
                "ok": False,
                "message": "LM Studio 服务尚未运行，不能加载模型。",
                "detail": "请先点击“启动服务”，确认状态为运行中后重试。",
            }
        try:
            result = subprocess.run(
                [
                    self._lms,
                    "load",
                    model_name,
                    "--context-length",
                    str(context_length),
                    "--identifier",
                    expected_identifier,
                    "--yes",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "message": "模型加载失败。",
                    "detail": _safe_cli_detail(
                        result.stderr or result.stdout,
                        "请检查可用内存/显存，或先卸载其他模型。",
                    ),
                }
            if expected_identifier not in self.server_status().get("loaded", []):
                return {
                    "ok": False,
                    "message": "模型加载命令已结束，但本地 API 尚未确认模型就绪。",
                    "detail": "请稍后刷新；若仍未就绪，请检查可用内存/显存。",
                }
            return {"ok": True, "message": f"已加载 {expected_identifier}（上下文 {context_length}）。"}
        except subprocess.TimeoutExpired:
            if expected_identifier in self.server_status().get("loaded", []):
                return {"ok": True, "message": f"已加载 {expected_identifier}（上下文 {context_length}）。"}
            return {
                "ok": False,
                "message": f"加载 {model_name} 超时，模型尚未就绪。",
                "detail": "已停止等待；请检查可用内存/显存，或稍后刷新模型状态。",
            }
        except OSError:
            return {"ok": False, "message": "无法执行 LM Studio 模型加载命令。"}

    def unload_model(self, model_name: str) -> dict:
        if self._lms is None:
            return {"ok": False, "message": "没有找到 lms CLI，无法卸载模型。"}
        if not self.server_status().get("running"):
            return {"ok": True, "message": "服务已停止，模型当前未加载。"}
        if model_name not in self.loaded_models():
            return {"ok": True, "message": f"{model_name} 当前未加载。"}
        try:
            result = self._run_cli(["unload", model_name], timeout=30)
            if result is None:
                return {"ok": False, "message": "模型卸载超时，请稍后刷新确认。"}
            if result.returncode != 0:
                return {
                    "ok": False,
                    "message": "模型卸载失败。",
                    "detail": _safe_cli_detail(result.stderr or result.stdout, "请稍后重试。"),
                }
            return {"ok": True, "message": f"已卸载 {model_name}。"}
        except (subprocess.SubprocessError, OSError):
            return {"ok": False, "message": "无法执行 LM Studio 模型卸载命令。"}


class OllamaManager(LocalModelManager):
    def __init__(self, base_url: str) -> None:
        self.provider = "ollama"
        self.base_url = base_url.rstrip("/").removesuffix("/api/chat").removesuffix("/api/tags").removesuffix("/v1")
        self._ollama = _find_executable("ollama", _resolve_user_home() / "AppData/Local/Programs/Ollama/ollama.exe")

    def _status(self) -> dict:
        installed = self._ollama is not None
        base = {"installed": installed, "install_url": "https://ollama.com/download/windows"}
        try:
            with httpx.Client(timeout=2, trust_env=False) as client:
                response = client.get(f"{self.base_url}/api/ps")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                    raise ValueError("invalid models response")
                loaded = [item["name"] for item in payload["models"]]
            return {**base, "installed": True, "running": True, "message": f"Ollama 服务已运行，已加载 {len(loaded)} 个模型。", "loaded": loaded}
        except httpx.HTTPStatusError as error:
            return {**base, "running": False, "blocked": True,
                    "message": f"配置端口有响应，但 Ollama 接口不可用（HTTP {error.response.status_code}）。请检查地址和端口占用。"}
        except (KeyError, TypeError, ValueError):
            return {**base, "running": False, "blocked": True, "message": "配置端口返回的不是 Ollama 模型状态，请检查是否被其他服务占用。"}
        except httpx.RequestError:
            return {**base, "running": False, "message": "Ollama 服务未运行，可以启动服务。" if installed else
                    "没有找到 Ollama 的启动程序。请安装并打开一次 Ollama，再回来刷新；已安装时也可先手动打开服务。"}

    def server_status(self) -> dict:
        return self._status()

    def start_server(self) -> dict:
        status = self._status()
        if status.get("running"):
            return {"ok": True, "message": "Ollama 服务已运行，无需重复启动。"}
        if status.get("blocked"):
            return {"ok": False, "message": status["message"]}
        if self._ollama is None:
            return {"ok": False, "message": "没有找到 Ollama 启动程序。", "detail": "请安装并打开一次 Ollama，再回来刷新；已安装时可先从开始菜单打开它。"}
        address = urlparse(self.base_url)
        if address.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return {"ok": False, "message": "此处只能启动本机 Ollama。", "detail": "请在模型服务所在电脑上启动它。"}
        lines = deque(maxlen=32)
        try:
            environment = os.environ.copy()
            environment["OLLAMA_HOST"] = address.netloc
            process = subprocess.Popen(
                [self._ollama, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            def collect():
                if process.stderr is not None:
                    try:
                        for line in iter(lambda: process.stderr.readline(512), b""):
                            lines.append(line.decode("utf-8", errors="replace"))
                    finally:
                        process.stderr.close()
            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            for _ in range(12):
                status = self._status()
                if status.get("running"):
                    return {"ok": True, "message": "Ollama 服务已启动并通过就绪检查。"}
                if process.poll() is not None:
                    reader.join(timeout=0.2)
                    detail = safe_diagnostic("".join(lines))
                    return {"ok": False, "message": f"Ollama 启动进程已退出（代码 {process.returncode}）。",
                            "detail": _safe_cli_detail(detail, "请检查服务地址、端口占用或重新打开 Ollama。") + (f"\n{detail}" if detail else "")}
                time.sleep(0.25)
            return {"ok": False, "message": "Ollama 启动后尚未就绪。", "detail": "进程可能仍在初始化，请稍后刷新；若持续未运行，请检查服务端口。"}
        except OSError as error:
            return {"ok": False, "message": "无法启动 Ollama。", "detail": safe_diagnostic(str(error))}

    def stop_server(self) -> dict:
        return {
            "ok": False,
            "message": "Ollama 服务由 Ollama 应用管理，请直接退出 Ollama 应用，或在任务管理器中结束 ollama 进程。",
        }

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=5, trust_env=False) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                return [item["name"] for item in response.json().get("models", [])]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return []

    def loaded_models(self) -> list[str]:
        return self._status().get("loaded", [])

    def load_model(
        self,
        model_name: str,
        context_length: int = 8192,
        *,
        identifier: str | None = None,
    ) -> dict:
        try:
            with httpx.Client(timeout=120, trust_env=False) as client:
                response = client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": "",
                        "stream": False,
                        "keep_alive": "30m",
                        "options": {"num_ctx": context_length},
                    },
                )
                response.raise_for_status()
            return {"ok": True, "message": f"已预热加载 {model_name}（上下文 {context_length}）。"}
        except httpx.HTTPStatusError as error:
            return {"ok": False, "message": f"Ollama 拒绝加载（{error.response.status_code}）。"}
        except httpx.RequestError:
            return {"ok": False, "message": "无法连接 Ollama 服务。"}

    def unload_model(self, model_name: str) -> dict:
        try:
            with httpx.Client(timeout=30, trust_env=False) as client:
                response = client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model_name, "prompt": "", "stream": False, "keep_alive": 0},
                )
                response.raise_for_status()
            return {"ok": True, "message": f"已卸载 {model_name}。"}
        except httpx.HTTPStatusError as error:
            return {"ok": False, "message": f"Ollama 拒绝卸载（{error.response.status_code}）。"}
        except httpx.RequestError:
            return {"ok": False, "message": "无法连接 Ollama 服务。"}


def build_local_model_manager(provider: str, base_url: str) -> LocalModelManager:
    if provider == "lm_studio":
        return LmStudioManager(base_url)
    if provider == "ollama":
        return OllamaManager(base_url)
    raise ValueError(f"本地模型管理不支持 {provider}（仅 lm_studio / ollama）。")


def _resolve_user_home() -> Path:
    """解析真实用户主目录，不依赖 USERPROFILE 环境变量（受限宿主常缺失该变量）。

    回退链：USERPROFILE → HOMEDRIVE+HOMEPATH → Windows API SHGetFolderPathW(CSIDL_PROFILE) → Path.home()
    """
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    drive = os.environ.get("HOMEDRIVE")
    path = os.environ.get("HOMEPATH")
    if drive and path:
        return Path(drive + path)
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(1024)
            # CSIDL_PROFILE = 0x28
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buf):
                return Path(buf.value)
        except Exception:
            pass
    return Path.home()


def _find_executable(name: str, known_path: Path | None) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if known_path is not None and known_path.exists():
        return str(known_path)
    return None


def _parse_cli_lines(output: str) -> list[str]:
    lines = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 去除表头与分隔符行；模型 id 通常是第一列。
        if line.startswith("─") or line.startswith("-") or line.startswith("="):
            continue
        # 跳过 lms ls 的表头行（LLM / PARAMS / ARCH / SIZE / DEVICE）与统计行
        if any(keyword in line for keyword in ("PARAMS", "ARCH", "SIZE", "DEVICE", "models, taking up")):
            continue
        lines.append(line.split("\t")[0].split()[0])
    return lines


def _parse_cli_models(output: str, *, include_identifiers: bool = False) -> list[str]:
    """优先读取 lms 的稳定 JSON 输出，兼容旧版 CLI 的表格文本。"""
    try:
        items = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return _parse_cli_lines(output)
    if not isinstance(items, list):
        return []
    models: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_key = item.get("modelKey") or item.get("identifier")
        if isinstance(model_key, str) and model_key:
            models.append(model_key)
        identifier = item.get("identifier")
        if (
            include_identifiers
            and isinstance(identifier, str)
            and identifier
            and identifier not in models
        ):
            models.append(identifier)
    return models


def _safe_cli_detail(output: str, fallback: str) -> str:
    """把第三方 CLI 输出归类为可操作提示，不回显本机路径或内部日志。"""
    normalized = output.lower()
    if "address already in use" in normalized or "eaddrinuse" in normalized or "port" in normalized and "use" in normalized:
        return "配置端口已被其他程序占用，请关闭占用程序或修改模型方案端口。"
    if any(word in normalized for word in ("out of memory", "insufficient memory", "vram", "resource guardrail")):
        return "可用内存或显存不足，请先卸载其他模型，保持上下文 8192 后重试。"
    if any(word in normalized for word in ("not found", "no model", "does not exist")):
        return "没有找到指定模型，请重新执行一键配置或刷新模型列表。"
    if "unknown option" in normalized or "unknown command" in normalized:
        return "当前 LM Studio 版本过旧，请从官方页面升级后重试。"
    if any(word in normalized for word in ("permission", "eperm", "access denied")):
        return "当前 Windows 用户没有足够权限，请用安装本产品和 LM Studio 的同一用户重试。"
    if any(word in normalized for word in ("network", "timeout", "timed out", "connection")):
        return "网络连接失败或超时，请检查网络后再次点击重试。"
    return fallback
