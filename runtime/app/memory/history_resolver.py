"""History resolver — answer "昨天/之前让我做了什么" from structured execution history first."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from app.memory import memory_manager
from app.store.session_store import session_store

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_HISTORY_KEYWORDS = ("之前", "做过什么", "昨天", "前天", "今天", "历史", "回忆", "最近")
_META_FALLBACK_KEYWORDS = (
    "读取记忆", "你记得", "你还记得", "历史记录", "回忆", "我是谁", "你是谁",
    "让你做什么", "做过什么",
    "项目结构", "系统结构", "代码结构", "架构",
)
_ACTION_HINTS = (
    "创建", "新建", "写入", "写到", "修改", "改成", "改为", "替换",
    "删除", "删掉", "移除", "提醒", "打开", "执行", "运行", "启动",
    "保存", "下载", "安装", "桌面", "文件", "命令", "终端", "应用",
)


@dataclass(frozen=True)
class HistoryWindow:
    label: str
    start_utc: str | None
    end_utc: str | None


def looks_like_history_query(query: str) -> bool:
    lowered = query.lower()
    return any(keyword in lowered for keyword in _HISTORY_KEYWORDS)


def resolve_history(query: str, *, evidence_limit: int = 3) -> str:
    window = _parse_window(query)
    action_events = session_store.list_action_events(
        start_utc=window.start_utc,
        end_utc=window.end_utc,
        limit=12,
    )
    success_events = [event for event in action_events if event["status"] == "verified_success"]
    other_events = [event for event in action_events if event["status"] != "verified_success"]

    summary_lines: list[str] = []
    evidence_lines: list[str] = []

    if success_events:
        summary = f"{window.label}查到 {len(success_events)} 条已验证执行记录"
        if other_events:
            summary += f"，另有 {len(other_events)} 条未成功记录"
        summary_lines.append(summary + "。")
        for event in success_events[:evidence_limit]:
            evidence_lines.append(_format_action_event(event))
        remaining = max(0, evidence_limit - len(evidence_lines))
        for event in other_events[:remaining]:
            evidence_lines.append(_format_action_event(event))
        return _render_memory_blocks(summary_lines, evidence_lines)

    summary_lines.append(f"{window.label}没有查到已验证执行记录。")
    if other_events:
        summary_lines.append(f"不过同一时间段还有 {len(other_events)} 条未成功记录。")
        for event in other_events[:evidence_limit]:
            evidence_lines.append(_format_action_event(event))
        return _render_memory_blocks(summary_lines, evidence_lines)

    episode_lines = _episode_fallback(window, evidence_limit=evidence_limit)
    if episode_lines:
        summary_lines.append("情景记忆里提到过这些事，但它们不是已验证执行历史。")
        return _render_memory_blocks(summary_lines, episode_lines)

    legacy_lines = _legacy_fallback(window, evidence_limit=evidence_limit)
    if legacy_lines:
        summary_lines.append("旧会话里提到过这些请求，但它们属于未验证旧记录。")
        return _render_memory_blocks(summary_lines, legacy_lines)

    summary_lines.append("当前也没有发现可参考的旧记录。")
    return _render_memory_blocks(summary_lines, [])


def _parse_window(query: str) -> HistoryWindow:
    now_local = datetime.now(_LOCAL_TZ)
    today = now_local.date()

    if "前天" in query:
        target = today - timedelta(days=2)
        return _day_window("前天", target)
    if "昨天" in query or "昨晚" in query:
        target = today - timedelta(days=1)
        return _day_window("昨天", target)
    if "今天" in query or "今日" in query:
        return _day_window("今天", today)
    return HistoryWindow(label="最近", start_utc=None, end_utc=None)


def _day_window(label: str, day) -> HistoryWindow:
    start_local = datetime.combine(day, time.min, tzinfo=_LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    return HistoryWindow(
        label=label,
        start_utc=start_local.astimezone(ZoneInfo("UTC")).isoformat(),
        end_utc=end_local.astimezone(ZoneInfo("UTC")).isoformat(),
    )


def _format_action_event(event: dict[str, object]) -> str:
    status = str(event["status"])
    if status == "verified_success":
        tag = "已执行"
    elif status == "verified_failure":
        tag = "失败"
    elif status == "cancelled":
        tag = "已取消"
    else:
        tag = "受阻"

    text = _clip(str(event["user_request"]), 42)
    line = f"- {event['local_time']} [{tag}] {text}"
    if status != "verified_success":
        summary = _clip(str(event["summary"]), 34)
        if summary:
            line += f"；{summary}"
    return line


def _episode_fallback(window: HistoryWindow, *, evidence_limit: int) -> list[str]:
    episodes = memory_manager.episodes_in_window(
        start_utc=window.start_utc,
        end_utc=window.end_utc,
        limit=12,
    )
    lines: list[str] = []
    for episode in episodes:
        summary = str(episode.get("summary", ""))
        if _is_meta_like(summary):
            continue
        line = f"- {_fmt_local_time(str(episode['ts']))} [情景记忆] {_clip(summary, 44)}"
        lines.append(line)
        if len(lines) >= evidence_limit:
            break
    return lines


def _legacy_fallback(window: HistoryWindow, *, evidence_limit: int) -> list[str]:
    rows = session_store.list_legacy_user_requests(
        start_utc=window.start_utc,
        end_utc=window.end_utc,
        limit=20,
    )
    lines: list[str] = []
    seen: set[str] = set()
    for row in rows:
        content = " ".join(str(row["content"]).split())
        if not content or _is_meta_like(content):
            continue
        if not _looks_action_like(content):
            continue
        key = re.sub(r"\s+", "", content)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {_fmt_local_time(str(row['ts']))} [旧记录，未验证] {_clip(content, 44)}")
        if len(lines) >= evidence_limit:
            break
    return lines


def _render_memory_blocks(summary_lines: list[str], evidence_lines: list[str]) -> str:
    parts: list[str] = []
    if summary_lines:
        parts.append("[记忆结论]\n" + "\n".join(summary_lines))
    if evidence_lines:
        parts.append("[少量依据]\n" + "\n".join(evidence_lines[:3]))
    return "\n\n".join(parts)


def _fmt_local_time(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts[11:16] if len(ts) >= 16 else ts
    return dt.astimezone(_LOCAL_TZ).strftime("%H:%M")


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _is_meta_like(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _META_FALLBACK_KEYWORDS)


def _looks_action_like(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _ACTION_HINTS)
