"""把内部异常转换成适合最终用户阅读的消息，同时保留完整日志。"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_CHINESE = re.compile(r"[\u3400-\u9fff]")
_TECHNICAL_MARKERS = (
    "traceback",
    "sqlalchemy",
    "sqlite",
    "errno",
    "winerror",
    "httperror",
    "urlerror",
    "connectionerror",
    "exception:",
    "file \"",
)


def public_error_message(error: Exception, fallback: str) -> str:
    """记录完整异常；仅放行不含明显技术细节的中文业务消息。"""
    logger.error("操作失败：%s", fallback, exc_info=(type(error), error, error.__traceback__))
    message = str(error).strip()
    lowered = message.lower()
    if (
        message
        and len(message) <= 240
        and _CHINESE.search(message)
        and not any(marker in lowered for marker in _TECHNICAL_MARKERS)
    ):
        return message
    return fallback
