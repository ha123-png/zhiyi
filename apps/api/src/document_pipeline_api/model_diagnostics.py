"""Bounded diagnostics for local inspection, with credentials and paths redacted."""
import json
import re

DIAGNOSTIC_LIMIT = 8192


def safe_diagnostic(message: str, *, secrets: tuple[str, ...] = (), limit: int = DIAGNOSTIC_LIMIT) -> str:
    text = str(message)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[凭证已隐藏]")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [已隐藏]", text)
    text = re.sub(r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|password|secret)\b[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+", r"\1[已隐藏]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[凭证已隐藏]", text)
    text = re.sub(r"(?i)https?://[^\s/?#]+", "[服务地址]", text)
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\r\n\"'<>]*", "[本机路径]", text)
    text = re.sub(r"/(?:home|Users|tmp|var)/[^\s\"'<>]+", "[本机路径]", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()
    if len(text) > limit:
        marker = "\n[诊断较长，中间部分已省略]\n"
        first = (limit - len(marker)) // 2
        text = text[:first] + marker + text[-(limit - first - len(marker)):]
    return text


def service_error_diagnostic(error, secret, *, limit=DIAGNOSTIC_LIMIT):
    # Services can return either plain text or a JSON object. Redact the known
    # credential in both literal and JSON-escaped form before truncating it.
    if isinstance(error, str):
        try:
            decoded = json.loads(error)
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(decoded, (dict, list, str)):
                error = decoded
    text = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
    secrets = (secret or "",)
    if secret:
        encoded = json.dumps(secret, ensure_ascii=False)[1:-1]
        ascii_encoded = json.dumps(secret, ensure_ascii=True)[1:-1]
        secrets += (
            encoded, ascii_encoded,
            encoded.replace("/", r"\/"), ascii_encoded.replace("/", r"\/"),
        )
    return safe_diagnostic(text, secrets=secrets, limit=limit)


def diagnostic_summary(message: str) -> str:
    text = safe_diagnostic(message).replace("\n", " ")
    return text if len(text) <= 512 else text[:475] + "…（展开诊断详情查看）"
