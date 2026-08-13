"""产品启动后自动恢复已激活的本机 Ollama 服务与模型（不阻塞页面）。

用户配置过 Ollama 方案后，重启产品时自动把服务拉起并把方案指定的模型
加载好，避免每次打开都要手动启动。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from document_pipeline_api.model_providers.local_model_manager import OllamaManager
from document_pipeline_api.services.model_runtime import (
    settings_for_active_profile_metadata,
)


def start_runtime_recovery(factory: sessionmaker[Session], settings) -> None:
    """产品启动后异步恢复已激活的本机 Ollama 服务与模型，不阻塞页面。"""
    threading.Thread(
        target=_recover_runtime,
        args=(factory, settings),
        daemon=True,
        name="local-model-runtime-recovery",
    ).start()


def _recover_runtime(
    factory: sessionmaker[Session],
    settings,
    *,
    manager_factory: Callable[[str], OllamaManager] = OllamaManager,
) -> None:
    try:
        with factory() as session:
            metadata = settings_for_active_profile_metadata(session, settings)
        if metadata.model_provider != "ollama":
            return
        manager = manager_factory(metadata.model_base_url)
        if manager._ollama is None:
            return
        started = manager.start_server()
        if not started.get("ok"):
            return
        if metadata.model_name in manager.server_status().get("loaded", []):
            return
        models = manager.list_models()
        model_key = next(
            (
                model
                for model in models
                if model == metadata.model_name
            ),
            None,
        )
        if model_key is not None:
            manager.load_model(
                model_key,
                context_length=metadata.model_context_length,
                identifier=metadata.model_name,
            )
    except Exception:
        # 恢复是尽力而为；失败由设置页和任务失败码继续给出可操作状态。
        return
