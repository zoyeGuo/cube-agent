"""Markdown debug trace for the latest request/tool execution."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any


def _trace_path() -> Path:
    return Path(__file__).resolve().parents[2] / "debug_tool_call_trace.md"


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _normalize(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(v) for v in value]
    return value


def _preview(text: str, limit: int = 400) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class MarkdownDebugTrace:
    """Human-readable audit trail for one request.

    This records observable decision summaries only. It does not expose raw
    private chain-of-thought.
    """

    def __init__(self, *, session_id: str, request_id: str, user_message: str) -> None:
        self.path = _trace_path()
        self._lines: list[str] = []
        self._step = 0
        self._write_header(session_id=session_id, request_id=request_id, user_message=user_message)

    def _write_header(self, *, session_id: str, request_id: str, user_message: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._lines = [
            "# Tool Call Debug Trace",
            "",
            f"- 生成时间: `{now}`",
            f"- Session: `{session_id}`",
            f"- Request: `{request_id}`",
            f"- 用户请求: `{user_message}`",
            "",
            "> 说明：本文件记录的是可审计的决策摘要、工具调用、参数、结果和验证过程，不包含模型私有思维链。",
            "",
        ]
        self._flush()

    def record(self, title: str, *, summary: str = "", data: Any | None = None) -> None:
        self._step += 1
        self._lines.append(f"## {self._step}. {title}")
        self._lines.append("")
        if summary:
            self._lines.append(summary)
            self._lines.append("")
        if data is not None:
            payload = _normalize(data)
            try:
                rendered = json.dumps(payload, ensure_ascii=False, indent=2)
            except TypeError:
                rendered = json.dumps(str(payload), ensure_ascii=False, indent=2)
            self._lines.append("```json")
            self._lines.append(rendered)
            self._lines.append("```")
            self._lines.append("")
        self._flush()

    def record_text(self, title: str, text: str) -> None:
        self.record(title, summary=_preview(text))

    def finalize(self, *, status: str, final_reply: str = "", spoken_text: str = "") -> None:
        payload = {"status": status}
        if final_reply:
            payload["final_reply_preview"] = _preview(final_reply, limit=600)
        if spoken_text:
            payload["spoken_text_preview"] = _preview(spoken_text, limit=300)
        self.record("结束", summary=f"本次请求已结束：{status}。", data=payload)

    def _flush(self) -> None:
        self.path.write_text("\n".join(self._lines).rstrip() + "\n", encoding="utf-8")

