"""Context compressor — mirrors Hermes's threshold-based compression strategy."""
import asyncio
import logging
from typing import Any

from app.core.model_adapter import call_once
from app.core.think import strip_think

logger = logging.getLogger(__name__)

# 保留头部轮数（开头关键上下文）和尾部轮数（最近上下文）
KEEP_HEAD_TURNS = 3   # 6 条消息
KEEP_TAIL_TURNS = 20  # 40 条消息
ROLLING_SUMMARY_KEEP_TURNS = 8
ROLLING_SUMMARY_TRIGGER_TURNS = 12

_SUMMARIZE_SYSTEM = "你是对话摘要助手，只输出摘要，不加任何解释。"
_ROLLING_SUMMARY_SYSTEM = "你是长期会话整理助手，只输出新的合并摘要，不加标题和解释。"

_SUMMARIZE_PROMPT = """请将以下对话历史压缩成一段结构化摘要（200字以内），保留所有关键信息、决定、结论。

对话历史：
{history}

要求：
- 保留重要事实和结论
- 去掉客套寒暄
- 用"用户...助手..."格式描述关键交互
- 纯文本，不加标题"""

_ROLLING_SUMMARY_PROMPT = """请把已有摘要和新增对话合并成一段新的会话摘要（260字以内）。

已有摘要：
{existing_summary}

新增对话：
{history}

要求：
- 保留用户目标、已完成事项、未完成事项、重要偏好和约定
- 去掉寒暄和重复表达
- 没有已有摘要时直接总结新增对话
- 纯文本，不加标题"""


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗估 token 数：CJK 字符 ~2char/token，其他 ~4char/token。"""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):  # tool message 可能是 list
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        cjk = sum(1 for c in content if "\u4e00" <= c <= "\u9fff")
        other = len(content) - cjk
        total += cjk // 2 + other // 4 + 4  # +4 per message overhead
    return total


def _format_for_summary(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        if m["role"] == "user":
            role = "用户"
        elif m["role"] == "system":
            role = "系统"
        else:
            role = "助手"
        content = m.get("content") or ""
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def should_compact_history(
    history: list[dict[str, Any]],
    *,
    keep_tail_turns: int = ROLLING_SUMMARY_KEEP_TURNS,
    trigger_turns: int = ROLLING_SUMMARY_TRIGGER_TURNS,
) -> bool:
    keep_tail_messages = max(keep_tail_turns * 2, 2)
    trigger_messages = max(trigger_turns * 2, keep_tail_messages + 2)
    return len(history) > trigger_messages


async def compact_history(
    existing_summary: str,
    history: list[dict[str, Any]],
    *,
    keep_tail_turns: int = ROLLING_SUMMARY_KEEP_TURNS,
    trigger_turns: int = ROLLING_SUMMARY_TRIGGER_TURNS,
) -> tuple[str, list[dict[str, Any]], bool]:
    if not should_compact_history(
        history,
        keep_tail_turns=keep_tail_turns,
        trigger_turns=trigger_turns,
    ):
        return existing_summary, history, False

    keep_tail_messages = max(keep_tail_turns * 2, 2)
    archived = history[:-keep_tail_messages]
    recent = history[-keep_tail_messages:]
    if not archived:
        return existing_summary, history, False

    logger.info("rolling-summary compacting %d archived messages", len(archived))
    merged_summary = await _merge_summary(existing_summary, archived)
    return merged_summary, recent, True


class ContextCompressor:
    def __init__(self, context_length: int, threshold: float = 0.50) -> None:
        self.context_length = context_length
        # 至少 64k，避免超小模型过早压缩
        self.threshold_tokens = max(int(context_length * threshold), 65536)
        self.warn_85 = int(context_length * 0.85)
        self.warn_95 = int(context_length * 0.95)

    def pressure_level(self, messages: list[dict[str, Any]]) -> str:
        tokens = _estimate_tokens(messages)
        if tokens >= self.warn_95:
            return "critical"    # 95%+ → 紧急
        if tokens >= self.warn_85:
            return "high"        # 85%+ → 警告
        if tokens >= self.threshold_tokens:
            return "compress"    # 50%+ → 触发压缩
        return "normal"

    async def compress(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        压缩策略（对齐 Hermes）：
          保留头部 KEEP_HEAD_TURNS 轮 + 尾部 KEEP_TAIL_TURNS 轮
          中间历史 → 结构化摘要
        """
        head_n = KEEP_HEAD_TURNS * 2   # role pairs
        tail_n = KEEP_TAIL_TURNS * 2

        if len(messages) <= head_n + tail_n:
            return messages  # 不够长，不压缩

        head = messages[:head_n]
        tail = messages[-tail_n:]
        middle = messages[head_n:-tail_n]

        if not middle:
            return messages

        logger.info("compressing %d middle messages", len(middle))

        summary_text = await _summarize(middle)
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": f"[历史摘要]\n{summary_text}",
        }

        compressed = head + [summary_msg] + tail
        after = _estimate_tokens(compressed)
        logger.info("compressed to ~%d tokens (was ~%d)", after, _estimate_tokens(messages))
        return compressed

    async def ensure_fits(
        self,
        messages: list[dict[str, Any]],
        max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        """
        多轮压缩直到 normal/high，或达到最大重试次数。
        对应 Hermes 的多次压缩 + 提示新建会话逻辑。
        """
        for attempt in range(max_retries):
            level = self.pressure_level(messages)
            if level in ("normal", "high"):
                break
            if level in ("compress", "critical"):
                logger.warning("context pressure=%s attempt=%d, compressing", level, attempt + 1)
                messages = await self.compress(messages)
        else:
            logger.error("context still over threshold after %d compressions", max_retries)
        return messages


async def _summarize(messages: list[dict[str, Any]]) -> str:
    history = _format_for_summary(messages)
    try:
        return strip_think(await call_once(
            messages=[{"role": "user", "content": _SUMMARIZE_PROMPT.format(history=history)}],
            system=_SUMMARIZE_SYSTEM,
            max_tokens=512,
        ))
    except Exception:  # noqa: BLE001
        # 摘要失败 → 降级：截取最后一条消息内容
        return f"（历史摘要生成失败，共 {len(messages)} 条消息被省略）"


async def _merge_summary(existing_summary: str, messages: list[dict[str, Any]]) -> str:
    history = _format_for_summary(messages)
    try:
        return strip_think(await call_once(
            messages=[{
                "role": "user",
                "content": _ROLLING_SUMMARY_PROMPT.format(
                    existing_summary=strip_think(existing_summary) or "（暂无）",
                    history=history,
                ),
            }],
            system=_ROLLING_SUMMARY_SYSTEM,
            max_tokens=384,
        ))
    except Exception:  # noqa: BLE001
        recent = _format_for_summary(messages[-4:])
        cleaned_existing = strip_think(existing_summary).strip()
        fallback_parts = [cleaned_existing] if cleaned_existing else []
        if recent.strip():
            fallback_parts.append(recent.replace("\n", "；"))
        return "；".join(part for part in fallback_parts if part)[:260] or "（历史摘要生成失败）"
