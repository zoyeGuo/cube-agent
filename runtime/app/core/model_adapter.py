"""MiniMax model adapter via OpenAI-compatible API."""
import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
_context_length: int | None = None
_FALLBACK_CONTEXT_LENGTH = 204_800
_MODEL_FALLBACK_DELAY_SECONDS = 1.2


def _make_http_client() -> httpx.AsyncClient:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
    return httpx.AsyncClient(proxy=proxy, timeout=60, trust_env=False)


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        hc = _make_http_client()
        _client = AsyncOpenAI(
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
            http_client=hc,
            max_retries=0,
        )
    return _client


def _parse_model_fallbacks(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _candidate_models() -> list[str]:
    models = [settings.model]
    explicit = _parse_model_fallbacks(settings.model_fallbacks)
    if explicit:
        models.extend(explicit)
    elif not settings.model.endswith("-highspeed"):
        models.append(f"{settings.model}-highspeed")

    seen: set[str] = set()
    ordered: list[str] = []
    for model in models:
        if model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def _is_overloaded_error(exc: Exception) -> bool:
    text = str(exc)
    lowered = text.lower()
    return (
        "error code: 529" in lowered
        or "overloaded_error" in lowered
        or "当前时段请求拥挤" in text
    )


async def _create_chat_completion(**kwargs: Any) -> Any:
    client = get_client()
    models = _candidate_models()
    last_exc: Exception | None = None

    for index, model in enumerate(models):
        try:
            if model != settings.model:
                logger.warning("switching to fallback model: %s", model)
            return await client.chat.completions.create(model=model, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_overloaded_error(exc) or index == len(models) - 1:
                raise
            delay = _MODEL_FALLBACK_DELAY_SECONDS * (index + 1)
            logger.warning(
                "model %s overloaded, retrying with fallback after %.1fs: %s",
                model,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("chat completion failed without an exception")


async def get_context_length() -> int:
    global _context_length
    if _context_length is not None:
        return _context_length
    try:
        client = get_client()
        models = await client.models.list()
        for m in models.data:
            if m.id == settings.model:
                ctx = (
                    getattr(m, "context_window", None)
                    or getattr(m, "context_length", None)
                    or getattr(m, "max_context_length", None)
                )
                if ctx:
                    _context_length = int(ctx)
                    logger.info("context_length from provider: %d", _context_length)
                    return _context_length
    except Exception:  # noqa: BLE001
        pass
    logger.info("context_length fallback: %d", _FALLBACK_CONTEXT_LENGTH)
    _context_length = _FALLBACK_CONTEXT_LENGTH
    return _context_length


# ── Tool call types ───────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_openai_message_part(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class TurnResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# ── Streaming ─────────────────────────────────────────────────────────────────

async def stream_with_tools(
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]] | None = None,
    on_text_delta: Callable[[str], None] | None = None,
) -> TurnResult:
    """
    Stream one turn. Calls on_text_delta for each visible text chunk.
    Filters <think> tags. Returns TurnResult with final text or tool_calls.
    """
    kwargs: dict[str, Any] = dict(
        max_tokens=settings.max_tokens,
        messages=[{"role": "system", "content": system_prompt}] + messages,
        stream=True,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    stream = await _create_chat_completion(**kwargs)  # type: ignore[arg-type]

    text_parts: list[str] = []
    tool_calls_acc: dict[int, dict[str, str]] = {}
    buf = ""
    in_think = False

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue
        delta = choice.delta

        # ── Text delta ──────────────────────────────────────────────────────
        if delta.content:
            buf += delta.content

            if not in_think and "<think>" in buf:
                in_think = True

            if in_think and "</think>" in buf:
                in_think = False
                buf = re.sub(r"<think>.*?</think>", "", buf, flags=re.DOTALL)

            if not in_think and buf:
                if on_text_delta:
                    on_text_delta(buf)
                text_parts.append(buf)
                buf = ""

        # ── Tool call delta ─────────────────────────────────────────────────
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                if tc_delta.id:
                    tool_calls_acc[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_calls_acc[idx]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

    # Flush remaining text buffer
    if buf and not in_think:
        if on_text_delta:
            on_text_delta(buf)
        text_parts.append(buf)

    full_text = re.sub(
        r"<think>.*?</think>", "", "".join(text_parts), flags=re.DOTALL
    ).strip()

    # Parse accumulated tool calls
    tool_calls: list[ToolCall] = []
    for idx in sorted(tool_calls_acc):
        tc = tool_calls_acc[idx]
        try:
            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))

    return TurnResult(text=full_text, tool_calls=tool_calls)


async def call_once(
    messages: list[dict[str, Any]],
    system: str = "",
    max_tokens: int = 1024,
) -> str:
    """Non-streaming single call, returns full response text."""
    resp = await _create_chat_completion(
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}] + messages,  # type: ignore[arg-type]
        stream=False,
    )
    return resp.choices[0].message.content or ""
