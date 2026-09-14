"""Natural-language streaming and native tools, independent from extraction schemas."""

import json
import threading
from ipaddress import ip_address
from urllib.parse import urlparse
import httpx
from document_pipeline_api.model_diagnostics import safe_diagnostic


class VisionNotSupportedError(ValueError):
    """A service explicitly rejected image input; do not retry it as another model."""


class ConversationTransportError(ValueError):
    """Recoverable transport failure with a short message and redacted diagnostics."""

    def __init__(self, message, *, code, diagnostic):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


def connection_timeout_seconds(settings):
    # Match the model-profile distinction: localhost/loopback stays responsive;
    # remote TLS handshakes get a modest budget within the configured timeout.
    host = (urlparse(settings.model_base_url).hostname or "").rstrip(".").lower()
    local = host == "localhost"
    if not local:
        try:
            local = ip_address(host).is_loopback
        except ValueError:
            pass
    return min(5 if local else 15, settings.model_timeout_seconds)


def transport_failure(error, settings):
    if isinstance(error, httpx.ConnectTimeout):
        code, message = "chat_connect_timeout", "连接聊天服务超时，请检查网络或服务状态后重试。"
    elif isinstance(error, httpx.ConnectError):
        code, message = "chat_connect_failed", "无法连接聊天服务，请检查网络、服务地址和服务状态后重试。"
    elif isinstance(error, httpx.ReadTimeout):
        code, message = "chat_read_timeout", "等待聊天服务回复超时，已保留收到的内容。请稍后重试，或调高方案的超时时间。"
    elif isinstance(error, (httpx.WriteTimeout, httpx.WriteError)):
        code, message = "chat_send_failed", "发送问题时连接中断或超时，请检查网络后重新发送。"
    elif isinstance(error, httpx.PoolTimeout):
        code, message = "chat_connection_busy", "聊天连接繁忙，请稍后重试。"
    elif isinstance(error, (httpx.ReadError, httpx.RemoteProtocolError)):
        code, message = "chat_connection_interrupted", "聊天连接中断，已保留收到的内容。请检查网络或服务状态后重试。"
    else:
        code, message = "chat_transport_error", "未能完成聊天请求，请检查服务地址和网络后重试。"
    return ConversationTransportError(
        message, code=code,
        diagnostic=safe_diagnostic(
            type(error).__name__ + ": " + str(error),
            secrets=(settings.model_api_key or "",), limit=2000,
        ),
    )


def conversation_reasoning(settings):
    """Resolve chat-only controls; never change extraction/template settings.

    Unknown compatible servers may reject reasoning_effort even for ordinary
    non-reasoning models. Preserve their default unless explicitly configured.
    A request control is not proof that a server/model actually honored it.
    """
    explicit = (settings.model_reasoning_effort or "").strip().lower()
    effort = explicit or "none"
    enabled = effort not in {"none", "off", "disabled"}
    host = (urlparse(settings.model_base_url).hostname or "").rstrip(".").lower()
    model = settings.model_name.lower()
    if settings.model_provider == "ollama":
        # Qwen uses a boolean. GPT-OSS accepts levels and cannot fully disable
        # thinking; retain false on an off request and observe the response.
        think = effort if enabled and "gpt-oss" in model else enabled
        return {"think": think}, "on" if enabled else "off"
    if host.endswith(".aliyuncs.com") and model.startswith("qwen"):
        return {"enable_thinking": enabled}, "on" if enabled else "off"
    if host == "api.deepseek.com":
        control = {"thinking": {"type": "enabled" if enabled else "disabled"}}
        if enabled:
            control["reasoning_effort"] = effort
        return control, "on" if enabled else "off"
    if settings.model_provider == "lm_studio" or explicit:
        return {"reasoning_effort": effort if enabled else "none"}, "on" if enabled else "off"
    return {}, "provider"


class ConversationProvider:
    def __init__(self, settings, cancel: threading.Event):
        self.settings = settings
        self.cancel = cancel
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.model_timeout_seconds, connect=connection_timeout_seconds(settings)),
            trust_env=False,
        )

    def close(self):
        self.client.close()

    def stream(self, messages, tools):
        try:
            yield from self._stream(messages, tools)
        except httpx.RequestError as error:
            if self.cancel.is_set():
                raise InterruptedError("已停止生成。") from error
            # Never replay a request: a timeout can occur after the service has
            # already accepted it or after partial content has reached the user.
            raise transport_failure(error, self.settings) from error

    def _stream(self, messages, tools):
        s = self.settings
        ollama = s.model_provider == "ollama"
        url = s.model_base_url.rstrip("/") + ("/api/chat" if ollama else "/chat/completions")
        if ollama:
            normalized = []
            for message in messages:
                if isinstance(message.get("content"), list):
                    normalized.append(
                        {
                            **message,
                            "content": "\n".join(
                                p["text"] for p in message["content"] if p["type"] == "text"
                            ),
                            "images": [
                                p["image_url"]["url"].split(",", 1)[1]
                                for p in message["content"]
                                if p["type"] == "image_url"
                            ],
                        }
                    )
                else:
                    normalized.append(message)
            messages = normalized
        payload = {"model": s.model_name, "messages": messages, "stream": True}
        reasoning_control, reasoning_requested = conversation_reasoning(s)
        payload.update(reasoning_control)
        if tools:
            payload["tools"] = tools
        host = urlparse(s.model_base_url).hostname or ""
        if (
            host.endswith(".aliyuncs.com")
            and s.model_name in {"qwen3.6-flash", "qwen3.6-flash-2026-04-16"}
            and tools
            and messages
            and messages[0].get("role") == "system"
            and isinstance(messages[0].get("content"), str)
        ):
            prefix = messages[0]["content"] + json.dumps(tools, ensure_ascii=False)
            # Reuse the provider's cache for a substantial static tool/system prefix.
            # Never pad prompts to reach the minimum and never mutate stored history.
            if sum(1.3 if ord(c) > 127 else 0.3 for c in prefix) >= 1536:
                payload["messages"] = [
                    {
                        **messages[0],
                        "content": [
                            {
                                "type": "text",
                                "text": messages[0]["content"],
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    },
                    *messages[1:],
                ]
                # The current user request and preceding history stay identical
                # throughout its tool loop. Cache that boundary as well, rather
                # than repeatedly charging the uncached history on every step.
                # Keep the system boundary for reuse between unrelated turns.
                for index in range(len(messages) - 1, 0, -1):
                    message = messages[index]
                    if message.get("role") == "user":
                        if isinstance(message.get("content"), str):
                            payload["messages"][index] = {
                                **message,
                                "content": [
                                    {
                                        "type": "text",
                                        "text": message["content"],
                                        "cache_control": {"type": "ephemeral"},
                                    }
                                ],
                            }
                        break
        if ollama:
            payload["options"] = {"num_ctx": s.model_context_length}
            if s.model_temperature is not None:
                payload["options"]["temperature"] = s.model_temperature
        else:
            # Known provider supports streaming usage; do not impose this extension
            # on arbitrary OpenAI-compatible local servers.
            if (
                s.model_provider == "lm_studio"
                or host == "api.deepseek.com"
                or (urlparse(s.model_base_url).hostname or "").endswith(".aliyuncs.com")
            ):
                payload["stream_options"] = {"include_usage": True}
            if s.model_temperature is not None:
                payload["temperature"] = s.model_temperature
        headers = {"Authorization": "Bearer " + s.model_api_key} if s.model_api_key else {}
        with self.client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                diagnostic = bytearray()
                for chunk in response.iter_bytes():
                    diagnostic.extend(chunk[: max(0, 6000 - len(diagnostic))])
                    if len(diagnostic) >= 6000:
                        break
                detail = safe_diagnostic(diagnostic.decode("utf-8", errors="replace")[:2000])
                has_images = any(
                    isinstance(m.get("content"), list)
                    and any(p.get("type") == "image_url" for p in m["content"])
                    or m.get("images")
                    for m in messages
                )
                lower = detail.lower()
                if (
                    response.status_code in {400, 422}
                    and has_images
                    and (
                        any(w in lower for w in ("image", "vision", "multimodal", "视觉", "图像"))
                        and any(
                            w in lower
                            for w in (
                                "not support",
                                "unsupported",
                                "does not",
                                "不支持",
                                "text-only",
                                "text only",
                            )
                        )
                    )
                ):
                    raise VisionNotSupportedError("当前模型服务不支持图像输入。")
                raise ValueError(
                    f"聊天服务拒绝请求（{response.status_code}）："
                    + detail
                    + "。请检查模型是否支持对话及原生工具调用。"
                )
            calls = {}
            completed = False
            produced = False
            received = 0
            truncated = False
            reasoning_characters = 0
            reasoning_observed = False
            reasoning_notice_sent = False
            for line in response.iter_lines():
                received += len(line)
                if received > 1_000_000:
                    raise ValueError("模型流式响应超过本轮大小上限，已保留收到的内容。")
                if self.cancel.is_set():
                    raise InterruptedError("已停止生成。")
                if not line or line.startswith(":"):
                    continue
                if not ollama:
                    if not line.startswith("data:"):
                        continue
                    line = line[5:].strip()
                    if line == "[DONE]":
                        completed = True
                        break
                item = json.loads(line)
                if not ollama and any(
                    c.get("finish_reason") == "length" for c in item.get("choices", [])
                ):
                    truncated = True
                if item.get("error"):
                    raise ValueError("聊天服务返回错误：" + safe_diagnostic(str(item["error"])))
                if item.get("usage"):
                    yield {"type": "usage", "usage": item["usage"]}
                if ollama and item.get("done") and "prompt_eval_count" in item:
                    prompt = item["prompt_eval_count"]
                    completion = item.get("eval_count", 0)
                    yield {
                        "type": "usage",
                        "usage": {
                            "prompt_tokens": prompt,
                            "completion_tokens": completion,
                            "total_tokens": prompt + completion,
                        },
                    }
                delta = (
                    item.get("message", {})
                    if ollama
                    else (item.get("choices") or [{}])[0].get("delta", {})
                )
                # Service-specific reasoning channels are not user answers or
                # tool evidence. Keep only counts, never the private text.
                fragments = {
                    value for key in ("reasoning_content", "reasoning", "thinking")
                    if isinstance(value := delta.get(key), str) and value.strip()
                }
                token_details = (item.get("usage") or {}).get("completion_tokens_details") or {}
                reported_tokens = token_details.get("reasoning_tokens")
                if fragments or (
                    isinstance(reported_tokens, int) and not isinstance(reported_tokens, bool)
                    and reported_tokens > 0
                ):
                    reasoning_observed = True
                    reasoning_characters += sum(map(len, fragments))
                    yield {"type": "usage", "usage": {
                        "reasoning_requested": reasoning_requested,
                        "reasoning_observed": True,
                        "reasoning_characters": reasoning_characters,
                    }}
                    if reasoning_requested == "off" and not reasoning_notice_sent:
                        reasoning_notice_sent = True
                        yield {
                            "type": "notice",
                            "message": "模型服务仍在返回思考内容，关闭设置可能未生效。",
                        }
                if delta.get("content"):
                    produced = True
                    yield {"type": "text", "text": delta["content"]}
                for index, call in enumerate(delta.get("tool_calls") or []):
                    slot = call.get("index", index)
                    state = calls.setdefault(
                        slot, {"id": call.get("id") or f"call_{slot}", "name": "", "arguments": ""}
                    )
                    if call.get("id"):
                        state["id"] = call["id"]
                    fn = call.get("function", {})
                    if fn.get("name"):
                        state["name"] += fn["name"]
                    args = fn.get("arguments", "")
                    state["arguments"] += (
                        json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else args
                    )
                if ollama and item.get("done"):
                    completed = True
                    yield {
                        "type": "usage",
                        "usage": {
                            k: item[k] for k in ("prompt_eval_count", "eval_count") if k in item
                        },
                    }
            yield {"type": "usage", "usage": {
                "reasoning_requested": reasoning_requested,
                "reasoning_observed": reasoning_observed,
                "reasoning_characters": reasoning_characters,
            }}
            if truncated:
                raise ValueError(
                    "模型回答达到输出长度限制，未将不完整的工具参数用于执行。请缩小本次问题；已保留收到的文字。"
                )
            if not completed:
                raise ValueError("聊天连接提前结束，已保存收到的内容。请重新提问。")
            # Validate the whole completed batch before exposing any executable
            # request, including when a later parallel call has broken JSON.
            parsed_calls = [
                {**call, "arguments": json.loads(call["arguments"] or "{}")}
                for call in calls.values()
            ]
            for call in parsed_calls:
                yield {"type": "tool", **call}
            if not produced and not calls:
                raise ValueError("模型没有返回可显示的回答或工具调用，请检查模型兼容性。")
