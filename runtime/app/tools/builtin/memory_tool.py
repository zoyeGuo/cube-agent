# -*- coding: utf-8 -*-
"""read_memory tool — expose persisted long-term memory and recent history."""
from datetime import datetime

from app.memory.history_resolver import looks_like_history_query, resolve_history
from app.memory import memory_manager
from app.memory.soul import soul_manager
from app.store.session_store import session_store
from app.tools.registry import tool

_IDENTITY_KEYWORDS = ("我是谁", "你是谁", "名字", "称呼", "叫我", "叫你")
_PREFERENCE_KEYWORDS = ("偏好", "喜欢", "不喜欢", "习惯", "风格", "尽量", "默认")
_PROJECT_KEYWORDS = ("项目", "任务", "在做", "正在做", "需求", "进度")
_HISTORY_KEYWORDS = ("之前", "做过什么", "昨天", "历史", "回忆", "最近")
_DETAIL_KEYWORDS = ("详细", "具体", "展开", "依据", "证据", "原文", "完整", "全部", "列出")


def _fmt_ts(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _query_topic(query: str) -> str:
    if any(keyword in query for keyword in _IDENTITY_KEYWORDS):
        return "identity"
    if any(keyword in query for keyword in _PREFERENCE_KEYWORDS):
        return "preferences"
    if any(keyword in query for keyword in _PROJECT_KEYWORDS):
        return "projects"
    if any(keyword in query for keyword in _HISTORY_KEYWORDS):
        return "history"
    return "overview"


def _wants_detail(query: str) -> bool:
    return any(keyword in query for keyword in _DETAIL_KEYWORDS)


def _top_items(items: list[str], limit: int = 3) -> list[str]:
    return [item for item in items if item][:limit]


def _episode_briefs(episodes: list[dict], limit: int = 2) -> list[str]:
    return [_clip(ep["summary"], 34) for ep in episodes[:limit] if ep.get("summary")]


def _episode_lines(episodes: list[dict], limit: int = 3) -> list[str]:
    lines = []
    for ep in episodes[:limit]:
        mark = "重要" if ep.get("importance") == 2 else "最近"
        lines.append(f"- {mark}：{_fmt_ts(ep['ts'])}，{_clip(ep['summary'], 52)}")
    return lines


def _recent_message_summary(limit: int = 4) -> list[str]:
    messages = session_store.recent_messages(limit=limit * 2)
    lines: list[str] = []
    pending_user = ""
    for item in messages:
        if item["role"] == "user":
            pending_user = _clip(item["content"], 30)
        elif item["role"] == "assistant":
            if pending_user:
                lines.append(f"- 你提过“{pending_user}”，我当时回应了“{_clip(item['content'], 36)}”")
                pending_user = ""
            else:
                lines.append(f"- 最近我回应过“{_clip(item['content'], 36)}”")
        if len(lines) >= limit:
            break
    return lines


def _recent_message_briefs(limit: int = 2) -> list[str]:
    return [
        line[2:] if line.startswith("- ") else line
        for line in _recent_message_summary(limit)
    ]


def _overview_lines(user_sections: dict[str, list[str]], memory_sections: dict[str, list[str]]) -> list[str]:
    user_name = soul_manager.get_user_name()
    assistant_name = soul_manager.get_name()
    lines: list[str] = []
    if user_name or assistant_name:
        identity_parts = []
        if user_name:
            identity_parts.append(f"你叫 {user_name}")
        if assistant_name:
            identity_parts.append(f"我叫 {assistant_name}")
        lines.append("当前最稳定的身份记忆是：" + "，".join(identity_parts) + "。")
    if user_sections["preferences"]:
        lines.append("我记住的交流偏好包括：" + "；".join(_top_items(user_sections["preferences"], 2)) + "。")
    if memory_sections["projects"]:
        lines.append("我现在挂着的长期事项包括：" + "；".join(_top_items(memory_sections["projects"], 2)) + "。")
    if memory_sections["decisions"]:
        lines.append("已经沉淀下来的默认决定包括：" + "；".join(_top_items(memory_sections["decisions"], 2)) + "。")
    return lines


@tool
def read_memory(query: str = "") -> str:
    """读取当前长期记忆和最近历史。当用户问“你记得我是谁吗”“读取记忆”“之前让我做过什么”时调用。query: 可选，用于检索相关记忆。"""
    if query.strip() and looks_like_history_query(query):
        return resolve_history(query)

    topic = _query_topic(query)
    wants_detail = _wants_detail(query)
    user_sections = memory_manager.user_sections()
    memory_sections = memory_manager.memory_sections()
    parts: list[str] = []
    summary_lines: list[str] = []
    evidence_lines: list[str] = []

    episodes = (
        memory_manager.search_episodes(query, limit=4)
        if query.strip()
        else memory_manager.recent_episodes(limit=4)
    )

    if topic == "identity":
        user_name = soul_manager.get_user_name()
        assistant_name = soul_manager.get_name()
        if user_name or assistant_name:
            pieces = []
            if user_name:
                pieces.append(f"你是 {user_name}")
            if assistant_name:
                pieces.append(f"我是 {assistant_name}")
            summary_lines.append("当前身份记忆是：" + "，".join(pieces) + "。")
        identity_items = _top_items(user_sections["identity"], 3)
        if identity_items:
            summary_lines.append("用户身份侧的长期记录包括：" + "；".join(identity_items) + "。")
    elif topic == "preferences":
        preference_items = _top_items(user_sections["preferences"] + user_sections["habits"], 4)
        if preference_items:
            summary_lines.append("我记住的偏好/习惯主要是：" + "；".join(preference_items) + "。")
    elif topic == "projects":
        project_items = _top_items(memory_sections["projects"] + memory_sections["decisions"], 4)
        if project_items:
            summary_lines.append("和长期事项最相关的记忆是：" + "；".join(project_items) + "。")
    elif topic == "history":
        if episodes:
            summary_lines.append("最近能回忆到的事情主要有：" + "；".join(_episode_briefs(episodes, 2)) + "。")
        else:
            recent_briefs = _recent_message_briefs(2)
            if recent_briefs:
                summary_lines.append("长期情景记忆还不多，但最近对话里提到过：" + "；".join(recent_briefs) + "。")
            else:
                summary_lines.append("当前还没有足够的历史记录可供回忆。")
    else:
        summary_lines.extend(_overview_lines(user_sections, memory_sections))

    if not summary_lines and episodes:
        summary_lines.append("当前检索到的长期记忆主要集中在最近几段情景记录。")

    if episodes and wants_detail:
        evidence_lines.extend(_episode_lines(episodes, 3))
    elif topic in {"history", "overview"} and wants_detail:
        evidence_lines.extend(_recent_message_summary(3))

    if not summary_lines and not evidence_lines:
        return "暂无已保存的长期记忆或历史记录。"

    if summary_lines:
        parts.append("[记忆结论]\n" + "\n".join(summary_lines))
    if evidence_lines:
        parts.append("[少量依据]\n" + "\n".join(evidence_lines))

    if topic == "overview" and wants_detail:
        anchors = []
        if memory_sections["constraints"]:
            anchors.append("约束：" + "；".join(_top_items(memory_sections["constraints"], 2)))
        if memory_sections["facts"]:
            anchors.append("长期事实：" + "；".join(_top_items(memory_sections["facts"], 2)))
        if anchors:
            parts.append("[补充锚点]\n" + "\n".join(f"- {item}" for item in anchors))

    return "\n\n".join(parts)
