"""Event-driven orchestrator — tool-calling while loop + SSE streaming."""
import asyncio
import base64
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import traceback

_log = logging.getLogger(__name__)

from app.models.session import Session
from app.schemas.events import SessionEvent, StateEvent, SpeechEvent, AudioEvent, DoneEvent, ErrorEvent, ChoiceEvent, ChoiceItem, ClarificationEvent, ScheduleEvent, ReminderItem
from app.store.session_store import session_store
from app.store.clarification_store import clarification_store
from app.store.pending_action_store import pending_action_store
from app.store.request_store import request_store
from app.core.model_adapter import stream_with_tools, call_once, get_context_length, ToolCall
from app.core.action_engine import (
    maybe_handle_user_action,
    pending_action_from_tool_call,
    confirmation_choice_items,
    execute_pending_action_steps,
    finalize_tracked_tool_action,
    persist_interrupted_tool_action,
    tracks_tool_call_action,
)
from app.core.debug_trace import MarkdownDebugTrace
from app.core.context_compressor import ContextCompressor, compact_history, should_compact_history
from app.core.think import strip_think, has_think, THINK_INSTRUCTION
from app.services.tts_service import text_to_speech
from app.config import settings
from app.memory import memory_manager
from app.memory.extractor import extract_and_save, should_extract_immediately
from app.memory.skills import skills_manager
from app.memory.skill_generator import generate_skill
from app.memory.soul import soul_manager, ONBOARDING_STEP1, ONBOARDING_STEP2
from app.services.voice_recommender import recommend_voices, get_extra_voices, extract_soul_draft
from app.tools import tool_registry
from app.tools.executor import run_tools, run_tool_call
from app.tools.context import current_session_id
from app.tools.builtin.file_tool import inspect_directory, inspect_text_file
from app.core.verbal_guard import detect as detect_verbal

_ASK_USER_SENTINEL = "__ASK_USER__:"
_CONFIRM_SENTINEL = "__CONFIRM__:"
_SHOW_SCHEDULE_SENTINEL = "__SHOW_SCHEDULE__"
_MAX_REPLAN = 2

# 工具输出中表示失败的前缀
_FAILURE_PREFIXES = (
    "错误：", "失败：", "工具执行错误：", "权限错误：",
    "沙箱拒绝：", "执行超时", "不存在：", "不是文件：", "不是目录：",
)

_REPLAN_PROMPT = (
    "以下工具执行遇到问题，请重新规划并尝试其他方法完成用户需求：\n{failures}\n"
    "如果实在无法完成，请直接告知用户原因。"
)

_REFLECT_PROMPT = (
    "你刚刚完成了工具调用并生成了回复。请做结构化自检：\n"
    "用户原始需求：{user_message}\n"
    "你的回复：{reply}\n\n"
    "只输出紧凑 JSON，不要输出自然语言、不要代码块。\n"
    "如果回复完整地满足了需求，输出 {{\"status\":\"ok\"}}。\n"
    "如果有明显遗漏、事实不符、口头声称已完成但缺少真实执行，或还需要继续调用工具，"
    "输出 {{\"status\":\"needs_retry\",\"reason\":\"missing_tool|incomplete|mismatch|unsafe_claim\"}}。"
)

_MEMORY_INSTRUCTION = (
    "你具备长期记忆能力。身份设定、用户信息、长期记忆和相关情景记录会通过系统提示或工具提供。"
    "当用户要求“读取记忆”、回忆之前做过什么、确认自己是谁或确认你是谁时，"
    "不要说自己没有记忆、不要说每次对话都是独立的。"
    "如果需要查看完整记忆，优先调用 read_memory 工具。"
)

_ARCHITECTURE_INSTRUCTION = (
    "你也具备项目架构知识能力。仓库的模块分区、关键文件、接口、前后端连接关系会通过架构索引工具提供。"
    "当用户询问项目结构、系统架构、模块职责、接口位置、前后端如何连接、某个能力是怎么实现的时，"
    "不要凭印象臆测，优先调用 read_architecture 工具，再结合结果回答。"
    "回答时先给整体理解，再讲主链路，最后只补必要的关键文件定位。"
    "除非用户明确要求枚举文件，否则不要把模块清单原样堆给用户。"
)

_RETRIEVAL_RESPONSE_INSTRUCTION = (
    "当工具返回的是检索结果、架构索引、记忆记录、最近消息或其他原始材料时，"
    "不要整段照抄或逐条堆叠给用户。"
    "先提炼一句结论，再补最多 1 到 3 条最关键依据。"
    "默认控制在 3 到 5 句自然语言里，不要使用分节标题。"
    "回答要适合直接播报，避免成段文件路径、原始日志和冗长清单。"
)

_SENSITIVE_ACTION_INSTRUCTION = (
    "涉及写文件、删除文件、打开本地文件/应用、执行命令、执行会修改本地文件的代码等副作用操作时，"
    "必须先征求用户确认。"
    "相关工具默认会先返回确认问题；用户确认后，系统会自动续执行。"
    "如果用户拒绝、犹豫或没有明确同意，就不要执行。"
    "不要向用户暴露内部参数名、tool_call 或执行细节。"
)

_MEMORY_QUERY_KEYWORDS = (
    "读取记忆", "记忆", "你记得", "你还记得", "之前让我", "之前做过什么",
    "昨晚", "昨天", "以前", "历史记录", "回忆", "我是谁", "你是谁",
)

_ARCHITECTURE_QUERY_KEYWORDS = (
    "架构", "项目结构", "代码结构", "系统结构", "模块职责", "模块关系", "调用链",
    "链路", "流程", "工作原理", "实现方式", "怎么实现", "怎么工作的", "前后端",
    "接口在哪", "路由在哪", "入口文件", "代码架构", "系统架构",
)

_DETAIL_ANSWER_KEYWORDS = (
    "详细", "具体", "展开", "细讲", "细说", "列出", "枚举", "完整", "全部",
    "文件", "路径", "源码", "代码", "接口", "路由", "逐步",
)

_DETERMINISTIC_READ_TOOLS = {"list_directory", "read_file"}


@dataclass(frozen=True)
class _ReflectVerdict:
    status: str = "ok"
    reason: str = "ok"


def _looks_like_memory_query(text: str) -> bool:
    return any(keyword in text for keyword in _MEMORY_QUERY_KEYWORDS)


def _looks_like_architecture_query(text: str) -> bool:
    if any(keyword in text for keyword in _ARCHITECTURE_QUERY_KEYWORDS):
        return True
    if "前端" in text and "后端" in text:
        return True
    if ("项目" in text or "系统" in text or "代码" in text) and ("结构" in text or "实现" in text):
        return True
    if any(token in text for token in ("记忆", "会话", "工具", "提醒")) and any(token in text for token in ("实现", "链路", "流程", "怎么")):
        return True
    return False


def _wants_detailed_answer(text: str) -> bool:
    return any(keyword in text for keyword in _DETAIL_ANSWER_KEYWORDS)


def _looks_like_directory_listing_query(text: str) -> bool:
    lowered = text.lower()
    has_listing_intent = any(
        token in text
        for token in ("有哪些文件", "有什么文件", "文件列表", "列出", "看一下", "看看", "查看", "读取")
    )
    has_directory_target = (
        "桌面" in text
        or "desktop" in lowered
        or "目录" in text
        or "文件夹" in text
    )
    has_fileish_focus = any(token in text for token in ("文件", "目录", "文件夹", "条目"))
    mutating = any(token in text for token in ("创建", "新建", "生成", "删除", "改", "修改", "编辑", "打开"))
    return has_listing_intent and has_directory_target and has_fileish_focus and not mutating


def _looks_like_file_read_query(text: str) -> bool:
    lowered = text.lower()
    read_intent = any(
        token in text
        for token in ("读取", "读一下", "看看内容", "内容是什么", "文件内容", "里面写了什么", "内容写了什么")
    )
    pathish = (
        "桌面" in text
        or "desktop" in lowered
        or bool(re.search(r"[\"'“”‘’][^\"'“”‘’]+[\"'“”‘’]", text))
        or bool(re.search(r"((?:~|/)[^\s，。！？]+)", text))
    )
    mutating = any(token in text for token in ("创建", "新建", "生成", "删除", "改", "修改", "编辑", "写入", "追加"))
    return read_intent and pathish and not mutating


def _extract_directory_query_path(text: str) -> str | None:
    quoted = re.search(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]", text)
    if quoted and any(marker in quoted.group(1).lower() for marker in ("/", "desktop", "桌面")):
        return quoted.group(1)
    path_match = re.search(r"((?:~|/)[^\s，。！？]+)", text)
    if path_match:
        return path_match.group(1)
    if "桌面" in text or "desktop" in text.lower():
        return "桌面"
    return None


def _extract_file_query_path(text: str) -> str | None:
    quoted = re.search(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]", text)
    if quoted:
        value = quoted.group(1).strip()
        if re.search(r"[\w\-.一-龥]+\.[A-Za-z0-9]{1,8}", value) or "/" in value or "\\" in value:
            if "/" in value or "\\" in value:
                return value
            if "桌面" in text or "desktop" in text.lower():
                return f"桌面/{value}"
            return None
    path_match = re.search(r"((?:~|/)[^\s，。！？]+)", text)
    if path_match:
        return path_match.group(1)
    filename_match = re.search(r"([\w\-.一-龥]+\.[A-Za-z0-9]{1,8})", text)
    if filename_match and ("桌面" in text or "desktop" in text.lower()):
        filename = filename_match.group(1)
        return f"桌面/{filename}"
    return None


def _directory_label(path: str, resolved_path: str) -> str:
    if path == "桌面" or "桌面" in path or "desktop" in path.lower():
        return "桌面"
    return Path(resolved_path).name or resolved_path


def _compose_directory_listing_reply(query_text: str, listing: dict) -> str:
    if not listing.get("ok"):
        return str(listing.get("error", "读取目录失败"))
    label = _directory_label(_extract_directory_query_path(query_text) or listing["path"], listing["path"])
    entries = listing.get("entries", [])
    if not entries:
        return f"{label}下是空的。"

    files = [entry["name"] for entry in entries if entry["kind"] == "file"]
    dirs = [entry["name"] for entry in entries if entry["kind"] == "directory"]
    total_count = int(listing.get("total_count", len(entries)))
    parts = [f"{label}下共有 {total_count} 个条目"]
    if files:
        shown = "、".join(files[:8])
        suffix = " 等" if len(files) > 8 else ""
        parts.append(f"文件有：{shown}{suffix}")
    if dirs:
        shown = "、".join(dirs[:6])
        suffix = " 等" if len(dirs) > 6 else ""
        parts.append(f"目录有：{shown}{suffix}")
    if listing.get("truncated"):
        parts.append("只展示前 100 条")
    return "。".join(parts) + "。"


def _compose_read_file_reply(path: str, file_info: dict) -> str:
    if not file_info.get("ok"):
        return str(file_info.get("error", "读取文件失败"))
    content = str(file_info.get("content", ""))
    name = Path(file_info["path"]).name
    if not content:
        return f"{name} 目前是空文件。"
    return f"已读取 {name}，内容如下：\n{content}"


def _compose_deterministic_read_reply(user_message: str, tool_calls: list[ToolCall]) -> str | None:
    if not tool_calls or any(tc.name not in _DETERMINISTIC_READ_TOOLS for tc in tool_calls):
        return None
    if len(tool_calls) != 1:
        return None

    tc = tool_calls[0]
    path = str(tc.arguments.get("path", "")).strip()
    if not path:
        return None
    if tc.name == "list_directory":
        return _compose_directory_listing_reply(user_message, inspect_directory(path))
    if tc.name == "read_file":
        return _compose_read_file_reply(path, inspect_text_file(path))
    return None


def _parse_reflect_verdict(text: str) -> _ReflectVerdict:
    raw = text.strip()
    if not raw or raw.upper().startswith("OK"):
        return _ReflectVerdict()
    try:
        payload = json.loads(raw)
    except Exception:
        lowered = raw.lower()
        if any(token in lowered for token in ("missing_tool", "tool", "未执行", "口头声称")):
            return _ReflectVerdict(status="needs_retry", reason="missing_tool")
        if any(token in lowered for token in ("unsafe_claim", "不该说已完成", "不应声称完成")):
            return _ReflectVerdict(status="needs_retry", reason="unsafe_claim")
        if any(token in lowered for token in ("incomplete", "遗漏", "不完整")):
            return _ReflectVerdict(status="needs_retry", reason="incomplete")
        return _ReflectVerdict(status="needs_retry", reason="mismatch")

    status = str(payload.get("status", "ok")).strip().lower()
    if status != "needs_retry":
        return _ReflectVerdict()
    reason = str(payload.get("reason", "mismatch")).strip().lower()
    if reason not in {"missing_tool", "incomplete", "mismatch", "unsafe_claim"}:
        reason = "mismatch"
    return _ReflectVerdict(status="needs_retry", reason=reason)


def _reflection_retry_message(verdict: _ReflectVerdict) -> str:
    reason_prompts = {
        "missing_tool": "你上一条回复没有通过真实工具完成用户请求。",
        "incomplete": "你上一条回复还有明显遗漏。",
        "mismatch": "你上一条回复和工具结果或用户需求不一致。",
        "unsafe_claim": "你上一条回复过早声称已经完成。",
    }
    lead = reason_prompts.get(verdict.reason, reason_prompts["mismatch"])
    return (
        f"{lead} 请继续完成这轮任务。"
        "如果需要，继续调用工具；如果实际上还没完成，就明确说明未完成。"
        "不要输出自我反思、内部检查、tool_call 或执行细节。"
        "最终只输出面向用户的简洁答复。"
    )


async def _reflect(user_message: str, reply: str, messages: list) -> _ReflectVerdict:
    """One-shot self-reflection after tool use. Returns structured verdict only."""
    prompt = _REFLECT_PROMPT.format(user_message=user_message, reply=reply)
    try:
        check = await call_once(
            messages=messages + [{"role": "user", "content": prompt}],
            system="你是一个自检模块，输出极简。",
            max_tokens=128,
        )
        return _parse_reflect_verdict(check)
    except Exception:
        return _ReflectVerdict()


def _friendly_error_message(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()

    if "overloaded_error" in lowered or "error code: 529" in lowered or "当前时段请求拥挤" in text:
        return "模型服务当前拥挤，请稍后重试"
    if "timed out" in lowered or "timeout" in lowered:
        return "请求模型超时了，请重试"
    if "connection" in lowered or "network" in lowered:
        return "连接模型服务失败，请检查网络后重试"
    return "处理请求时发生异常，请重试"


def _sanitize_user_visible_text(text: str) -> str:
    cleaned = strip_think(text)
    cleaned = re.sub(r"\$?\s*confirmed\s*=\s*true", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"补充执行[:：]\s*<invoke[\s\S]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<invoke[\s\S]*?</invoke>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<invoke[\s\S]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?parameter[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_for_speech(text: str) -> str:
    cleaned = _sanitize_user_visible_text(text)
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*•]+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _normalize_for_display_lines(text: str) -> list[str]:
    cleaned = _sanitize_user_visible_text(text)
    cleaned = re.sub(r"^\[[^\]]+\]\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("`", "")
    lines: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[-*•]+\s*", "", line)
        line = re.sub(r"\s+", " ", line)
        if line.startswith(("生成时间：", "索引文件：", "查询：")):
            continue
        if line.startswith(("/Users/", "C:/", "D:/")):
            continue
        if re.search(r"\$?\s*confirmed\s*=\s*true", line, flags=re.IGNORECASE):
            continue
        if line:
            lines.append(line)
    return lines


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    return [part.strip() for part in parts if part.strip()]


def _is_location_heavy(line: str) -> bool:
    return any(token in line for token in ("runtime/app/", "voxel-avatar/", "/v1/", "http://", "ws://"))


def _should_distill_response(user_message: str, clean_text: str, tools_called: set[str]) -> bool:
    if {"read_memory", "read_architecture"} & tools_called:
        return True
    if _looks_like_memory_query(user_message) or _looks_like_architecture_query(user_message):
        return True
    return any(marker in clean_text for marker in ("[理解摘要]", "[记忆结论]", "[关键落点]", "[关键接口]", "[少量依据]"))


def _condense_for_display(text: str, *, detailed: bool = False, query_text: str = "") -> str:
    lines = _normalize_for_display_lines(text)
    if not lines:
        return ""

    max_lines = 5 if detailed else 3
    max_chars = 320 if detailed else 210
    selected: list[str] = []
    total = 0
    seen: set[str] = set()

    for line in lines:
        if query_text and line == query_text.strip():
            continue
        if not detailed and _is_location_heavy(line):
            continue
        key = re.sub(r"\s+", "", line)
        if key in seen:
            continue
        seen.add(key)
        if selected and total + len(line) > max_chars:
            break
        selected.append(line)
        total += len(line)
        if len(selected) >= max_lines:
            break

    if not selected:
        selected = lines[:max_lines]

    normalized: list[str] = []
    for line in selected:
        normalized.append(line if line.endswith(("。", "！", "？", "；")) else line + "。")
    return "".join(normalized)


def _condense_for_tts(text: str, *, aggressive: bool = False) -> str:
    cleaned = _normalize_for_speech(text)
    if not cleaned:
        return ""

    max_chars = 110 if aggressive else 170
    max_sentences = 2 if aggressive else 3
    if len(cleaned) <= max_chars:
        return cleaned

    sentences = _split_sentences(cleaned)
    selected: list[str] = []
    total = 0
    for sentence in sentences:
        if any(token in sentence for token in ("runtime/app/", "voxel-avatar/", "/v1/", "http://", "ws://")):
            continue
        if selected and total + len(sentence) > max_chars:
            break
        selected.append(sentence)
        total += len(sentence)
        if len(selected) >= max_sentences:
            break

    if not selected:
        shortened = cleaned[:max_chars].rstrip("，,；;。.!? ")
        return shortened + "。"
    return "".join(selected)


class RequestCancelled(Exception):
    """Raised when the active request is cancelled by the user or superseded."""


async def run(
    session: Session,
    user_message: str,
    queue: asyncio.Queue[str],
    request_id: str,
) -> None:
    # 注入 session_id 供工具使用
    current_session_id.set(session.session_id)
    debug_trace = MarkdownDebugTrace(
        session_id=session.session_id,
        request_id=request_id,
        user_message=user_message,
    )

    def emit(event) -> None:
        queue.put_nowait(f"data: {json.dumps(event.model_dump())}\n\n")

    def ensure_not_cancelled() -> None:
        if request_store.is_cancelled(request_id):
            raise RequestCancelled()

    try:
        debug_trace.record(
            "请求开始",
            summary="已开始处理这条请求。",
            data={"turn_count_before": session.turn_count, "history_length": len(session.history)},
        )
        emit(SessionEvent(session_id=session.session_id, request_id=request_id))
        emit(StateEvent(name="thinking", scope="cognition"))

        session.add_user(user_message)
        session_store.persist_message(session.session_id, "user", user_message)
        ensure_not_cancelled()

        if should_compact_history(
            session.history,
            keep_tail_turns=settings.session_recent_turns,
            trigger_turns=settings.session_summary_trigger_turns,
        ):
            emit(StateEvent(name="session_compacting", scope="system"))
            summary, trimmed_history, was_compacted = await compact_history(
                session.summary,
                session.history,
                keep_tail_turns=settings.session_recent_turns,
                trigger_turns=settings.session_summary_trigger_turns,
            )
            if was_compacted:
                session.summary = summary
                session.replace_history(trimmed_history)
                session_store.compact_history(session.session_id, session.summary, session.history)
        ensure_not_cancelled()

        # 压缩检查
        context_length = await get_context_length()
        compressor = ContextCompressor(context_length, threshold=settings.compression_threshold)
        messages = session.messages()

        level = compressor.pressure_level(messages)
        if level == "high":
            emit(StateEvent(name="context_high", scope="system"))
        elif level == "critical":
            emit(StateEvent(name="context_critical", scope="system"))
        if level in ("compress", "critical"):
            messages = await compressor.ensure_fits(messages)
        ensure_not_cancelled()

        # ── 人格 / 引导 ───────────────────────────────────────────────────────
        is_onboarding = not soul_manager.exists()
        onboarding_step = session.turn_count  # 0=首次, 1=用户刚描述

        if not is_onboarding and soul_manager.needs_identity_bootstrap():
            identity_sources = [
                session.summary,
                user_message,
                memory_manager.load_user(),
                "\n".join(
                    str(message.get("content", ""))
                    for message in session.history[-8:]
                    if message.get("role") in {"user", "assistant"}
                ),
                session_store.recent_identity_context(),
            ]
            soul_manager.sync_identity_from_text("\n".join(part for part in identity_sources if part))

        relevant_skills: list[dict] = []

        if is_onboarding:
            base = ONBOARDING_STEP1 if onboarding_step == 0 else ONBOARDING_STEP2
            system_prompt = base
        else:
            relevant_episodes = memory_manager.search_episodes(user_message, limit=4)
            relevant_skills = skills_manager.search_skills(user_message, limit=3)
            system_prompt = memory_manager.build_system_prompt(
                soul_manager.load(), settings.system_prompt,
                relevant_episodes=relevant_episodes or None,
                relevant_skills=relevant_skills or None,
            )
            system_prompt += "\n\n" + _MEMORY_INSTRUCTION
            system_prompt += "\n\n" + _ARCHITECTURE_INSTRUCTION
            system_prompt += "\n\n" + _RETRIEVAL_RESPONSE_INSTRUCTION
            system_prompt += "\n\n" + _SENSITIVE_ACTION_INSTRUCTION
            system_prompt += "\n\n" + THINK_INSTRUCTION
            if session.summary.strip():
                system_prompt += (
                    "\n\n以下是当前会话的历史摘要，请把它当作背景信息使用，不要逐字复述：\n"
                    + strip_think(session.summary)
                )
            if _looks_like_memory_query(user_message):
                system_prompt += "\n\n本轮用户正在询问记忆或历史，请优先调用 read_memory 工具，再基于结果回答。"
            if _looks_like_architecture_query(user_message):
                system_prompt += "\n\n本轮用户正在询问项目或代码架构，请优先调用 read_architecture 工具，再基于索引结果回答。"

        tool_schemas = tool_registry.get_schemas()

        # ── 工具调用循环 ──────────────────────────────────────────────────────
        assistant_text = ""
        tools_called: set[str] = set()
        verbal_retry_used = False
        reflection_retry_used = False
        replan_count = 0
        direct_tool_handled = False
        if not is_onboarding:
            action_result = await maybe_handle_user_action(
                session_id=session.session_id,
                user_message=user_message,
                emit=emit,
                ensure_not_cancelled=ensure_not_cancelled,
                debug_trace=debug_trace,
            )
            if action_result.handled:
                direct_tool_handled = True
                assistant_text = action_result.assistant_text
                tools_called |= action_result.tools_called
                debug_trace.record(
                    "动作引擎直达结果",
                    summary="这条请求由动作引擎直接处理完成。",
                    data={
                        "tools_called": sorted(action_result.tools_called),
                        "receipt": action_result.receipt,
                        "assistant_text": assistant_text,
                    },
                )
            elif _looks_like_directory_listing_query(user_message):
                directory_path = _extract_directory_query_path(user_message)
                if directory_path:
                    direct_tool_handled = True
                    tools_called.add("list_directory")
                    emit(StateEvent(name="tool_calling", scope="execution"))
                    assistant_text = _compose_directory_listing_reply(
                        user_message,
                        inspect_directory(directory_path),
                    )
                    debug_trace.record(
                        "确定性目录读取",
                        summary="目录读取由后端直接处理，绕过模型自由复述。",
                        data={"path": directory_path, "assistant_text": assistant_text},
                    )
            elif _looks_like_file_read_query(user_message):
                file_path = _extract_file_query_path(user_message)
                if file_path:
                    direct_tool_handled = True
                    tools_called.add("read_file")
                    emit(StateEvent(name="tool_calling", scope="execution"))
                    assistant_text = _compose_read_file_reply(
                        file_path,
                        inspect_text_file(file_path),
                    )
                    debug_trace.record(
                        "确定性文件读取",
                        summary="文件读取由后端直接处理，绕过模型自由复述。",
                        data={"path": file_path, "assistant_text": assistant_text},
                    )

        while not direct_tool_handled:
            text_buffer: list[str] = []
            emit(StateEvent(name="calling_model", scope="cognition"))

            result = await stream_with_tools(
                messages=messages,
                system_prompt=system_prompt,
                tools=tool_schemas or None,
                on_text_delta=lambda chunk: text_buffer.append(chunk),
            )
            ensure_not_cancelled()
            debug_trace.record(
                "模型返回",
                summary="收到本轮模型结果。",
                data={
                    "text_preview": result.text[:400],
                    "has_tool_calls": result.has_tool_calls,
                    "tool_calls": result.tool_calls,
                },
            )

            if result.has_tool_calls:
                emit(StateEvent(name="tool_calling", scope="execution"))

                for tc in result.tool_calls:
                    if (
                        tc.name == "read_memory"
                        and _looks_like_memory_query(user_message)
                        and not str(tc.arguments.get("query", "")).strip()
                    ):
                        tc.arguments["query"] = user_message
                    tools_called.add(tc.name)

                messages.append({
                    "role": "assistant",
                    "content": result.text or None,
                    "tool_calls": [tc.to_openai_message_part() for tc in result.tool_calls],
                })

                tracked_actions = {
                    tc.id: pending_action_from_tool_call(
                        tc,
                        "确认执行",
                        session_id=session.session_id,
                    )
                    for tc in result.tool_calls
                    if tracks_tool_call_action(tc)
                }
                outputs = await run_tools(result.tool_calls)
                ensure_not_cancelled()
                debug_trace.record(
                    "工具原始输出",
                    summary="模型请求的工具已经执行完成。",
                    data={"tool_calls": result.tool_calls, "outputs": outputs},
                )

                # ── Human-in-the-loop：ask_user 工具暂停等待用户回复 ──────────
                tool_messages = []
                for tc, output in zip(result.tool_calls, outputs):
                    if output.startswith(_CONFIRM_SENTINEL):
                        question = output[len(_CONFIRM_SENTINEL):].strip()
                        action = tracked_actions.get(tc.id) or pending_action_from_tool_call(
                            tc,
                            question or "确认执行",
                            session_id=session.session_id,
                        )
                        action.title = question or action.title
                        pending_action_store.create(session.session_id, action)
                        emit(ChoiceEvent(
                            choice_id="confirm_execution",
                            title=action.title,
                            items=confirmation_choice_items(action.intent),
                        ))
                        debug_trace.record(
                            "等待确认",
                            summary="工具执行需要用户确认，已弹出确认面板。",
                            data={"tool_call": tc, "question": question or action.title},
                        )
                        emit(StateEvent(name="waiting_user", scope="execution"))
                        resumed_action, answer = await pending_action_store.wait(session.session_id, timeout=300)
                        ensure_not_cancelled()
                        debug_trace.record(
                            "确认结果",
                            summary="收到用户对执行面板的选择。",
                            data={"tool_call": tc, "answer": answer or "", "action": resumed_action or action},
                        )
                        if answer == "confirm":
                            action_to_run = resumed_action or action
                            resumed_outputs = await execute_pending_action_steps(action_to_run)
                            debug_trace.record(
                                "确认后续执行",
                                summary="确认后继续执行工具步骤。",
                                data={"action": action_to_run, "outputs": resumed_outputs},
                            )
                            content, _ = await finalize_tracked_tool_action(
                                session_id=session.session_id,
                                user_request=user_message,
                                action=action_to_run,
                                outputs=resumed_outputs,
                                emit=emit,
                            )
                        else:
                            content = persist_interrupted_tool_action(
                                session_id=session.session_id,
                                user_request=user_message,
                                action=resumed_action or action,
                                answer=answer,
                            )
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": content,
                        })
                    elif output.startswith(_ASK_USER_SENTINEL):
                        question = output[len(_ASK_USER_SENTINEL):]
                        clarification_store.create(session.session_id)
                        emit(ClarificationEvent(question=question))
                        emit(StateEvent(name="waiting_user", scope="execution"))
                        answer = await clarification_store.wait(session.session_id, timeout=300)
                        ensure_not_cancelled()
                        if answer is None:
                            answer = "用户未在规定时间内回复，请基于已有信息继续或告知用户。"
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"用户回复：{answer}",
                        })
                    elif output == _SHOW_SCHEDULE_SENTINEL:
                        from app.services.scheduler import list_reminders
                        items = list_reminders(session.session_id)
                        emit(ScheduleEvent(reminders=[ReminderItem(**r) for r in items]))
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"已展示日程面板，共 {len(items)} 条提醒。",
                        })
                    else:
                        content = output
                        if tc.id in tracked_actions:
                            content, _ = await finalize_tracked_tool_action(
                                session_id=session.session_id,
                                user_request=user_message,
                                action=tracked_actions[tc.id],
                                outputs=[output],
                                emit=emit,
                            )
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": content,
                        })

                messages.extend(tool_messages)
                debug_trace.record(
                    "工具消息入链",
                    summary="工具结果已经回填到消息链路。",
                    data={"tool_messages": tool_messages},
                )

                deterministic_read_reply = None
                if _looks_like_directory_listing_query(user_message) or _looks_like_file_read_query(user_message):
                    deterministic_read_reply = _compose_deterministic_read_reply(user_message, result.tool_calls)
                if deterministic_read_reply is not None:
                    assistant_text = deterministic_read_reply
                    debug_trace.record(
                        "读取结果收口",
                        summary="读取型工具结果由后端确定性收口。",
                        data={"assistant_text": assistant_text},
                    )
                    break

                # ── set_reminder 成功后自动展示日程面板 ────────────────────────
                reminder_was_set = False
                for _tc, _tm in zip(result.tool_calls, tool_messages):
                    if _tc.name == "set_reminder" and _tc.id not in tracked_actions:
                        content = _tm.get("content", "")
                        failed = any(content.startswith(p) for p in _FAILURE_PREFIXES)
                        if not failed:
                            reminder_was_set = True
                        break
                _log.info("reminder_was_set=%s tools=%s", reminder_was_set, [tc.name for tc in result.tool_calls])
                if reminder_was_set:
                    from app.services.scheduler import list_reminders
                    items = list_reminders(session.session_id)
                    _log.info("emitting ScheduleEvent: %d items", len(items))
                    emit(ScheduleEvent(reminders=[ReminderItem(**r) for r in items]))
                ensure_not_cancelled()

                # ── 工具失败重规划 ─────────────────────────────────────────────
                if replan_count < _MAX_REPLAN:
                    failures = [
                        f"- {tc.name}: {out['content']}"
                        for tc, out in zip(result.tool_calls, tool_messages)
                        if any(out["content"].startswith(p) for p in _FAILURE_PREFIXES)
                    ]
                    if failures:
                        replan_count += 1
                        replan_msg = _REPLAN_PROMPT.format(failures="\n".join(failures))
                        messages.append({"role": "user", "content": replan_msg})
                        emit(StateEvent(name="replanning", scope="execution"))

                continue

            if _looks_like_memory_query(user_message):
                tools_called.add("read_memory")
                assistant_text = await run_tool_call(
                    ToolCall(
                        id="direct_read_memory",
                        name="read_memory",
                        arguments={"query": user_message},
                    ),
                    bypass_confirmation=True,
                )
                break

            # 检测口头执行（每轮只重试一次）
            if not verbal_retry_used:
                correction = detect_verbal(user_message, result.text, tools_called)
                if correction:
                    verbal_retry_used = True
                    debug_trace.record(
                        "口头执行拦截",
                        summary="检测到模型口头声称已完成，但没有满足执行约束，已要求重答。",
                        data={"correction": correction, "text_preview": result.text[:300]},
                    )
                    messages.append({"role": "assistant", "content": result.text})
                    messages.append({"role": "user", "content": correction})
                    continue

            if tools_called and not is_onboarding and not reflection_retry_used:
                clean = strip_think(result.text)
                verdict = await _reflect(user_message, clean, messages)
                debug_trace.record(
                    "结构化自检",
                    summary="工具调用后做了一次结构化自检。",
                    data=verdict,
                )
                if verdict.status == "needs_retry":
                    reflection_retry_used = True
                    messages.append({"role": "assistant", "content": clean})
                    messages.append({"role": "user", "content": _reflection_retry_message(verdict)})
                    emit(StateEvent(name="replanning", scope="execution"))
                    continue

            assistant_text = result.text
            break
        ensure_not_cancelled()

        # ── 剥离 think 块，准备输出 ────────────────────────────────────────────
        clean_text = _sanitize_user_visible_text(assistant_text)
        should_distill = _should_distill_response(user_message, clean_text, tools_called)
        display_text = clean_text
        if should_distill:
            display_text = _condense_for_display(
                clean_text,
                detailed=_wants_detailed_answer(user_message),
                query_text=user_message,
            ) or clean_text
        ensure_not_cancelled()
        spoken_text = _condense_for_tts(
            display_text,
            aggressive=(
                should_distill
                or len(display_text) > 220
            ),
        )

        # ── 收尾 ──────────────────────────────────────────────────────────────
        emit(StateEvent(name="speaking", scope="execution"))
        emit(SpeechEvent(text=display_text, chunk=False))
        ensure_not_cancelled()

        emit(StateEvent(name="generating_audio", scope="execution"))
        audio_bytes = await text_to_speech(spoken_text or display_text)
        ensure_not_cancelled()
        if audio_bytes:
            emit(AudioEvent(data=base64.b64encode(audio_bytes).decode()))

        history_text = display_text if should_distill else clean_text
        session.add_assistant(history_text)
        session_store.persist_message(session.session_id, "assistant", history_text)
        debug_trace.finalize(
            status="success",
            final_reply=display_text,
            spoken_text=spoken_text or display_text,
        )

        emit(DoneEvent(request_id=request_id))

        is_meta_query = _looks_like_memory_query(user_message) or _looks_like_architecture_query(user_message)
        retrieval_tools = {"read_memory", "read_architecture", "list_directory", "read_file"}
        should_persist_memory = (
            not is_onboarding
            and not is_meta_query
            and not (retrieval_tools & tools_called)
            and (
                should_extract_immediately(user_message, clean_text)
                or session.turn_count % settings.memory_extract_every == 0
            )
        )

        # ── 用户主动换声音 ────────────────────────────────────────────────────
        _voice_keywords = ("换声音", "换音色", "换个声音", "换个音色", "更换声音", "更换音色",
                           "选择音色", "选音色", "改变声音", "切换声音", "切换音色")
        _wants_voice = (
            "show_voice_panel" in tools_called
            or any(kw in user_message for kw in _voice_keywords)
        )
        if _wants_voice and not is_onboarding:
            recs = await recommend_voices(user_message)
            rec_ids = [r["id"] for r in recs]
            items = [
                ChoiceItem(id=r["id"], label=r["name"], tag=r.get("reason", ""), recommended=(i == 0))
                for i, r in enumerate(recs)
            ]
            extras = [
                ChoiceItem(id=v["id"], label=v["name"], tag=v["desc"])
                for v in get_extra_voices(rec_ids)
            ]
            emit(ChoiceEvent(
                choice_id="voice_selection", title="选择音色",
                items=items, extra_items=extras,
                current_id=soul_manager.get_voice_id(),
            ))

        # ── 引导完成：推荐音色 ─────────────────────────────────────────────────
        elif is_onboarding and onboarding_step >= 1:
            recs, soul_draft = await asyncio.gather(
                recommend_voices(user_message),
                extract_soul_draft(user_message),
            )
            soul_manager.create(
                soul_draft["name"], soul_draft["user_name"], soul_draft["personality"],
                voice_id="pending", voice_name="",
            )
            rec_ids = [r["id"] for r in recs]
            items = [
                ChoiceItem(id=r["id"], label=r["name"], tag=r.get("reason", ""), recommended=(i == 0))
                for i, r in enumerate(recs)
            ]
            extras = [
                ChoiceItem(id=v["id"], label=v["name"], tag=v["desc"])
                for v in get_extra_voices(rec_ids)
            ]
            emit(ChoiceEvent(
                choice_id="voice_selection", title="选择音色",
                items=items, extra_items=extras,
                current_id=None,
            ))

        # 后台记忆提取
        if should_persist_memory:
            asyncio.create_task(extract_and_save(session.history, memory_manager))

        # 后台技能生成（工具调用 ≥2 次时）
        if not is_onboarding and len(tools_called) >= 2:
            asyncio.create_task(
                generate_skill(user_message, tools_called, messages, clean_text, skills_manager)
            )
            # 本次用到的技能使用计数 +1
            for skill in relevant_skills:
                skills_manager.increment_use(skill["id"])

    except RequestCancelled:
        debug_trace.finalize(status="cancelled")
        _log.info("request cancelled: session=%s request=%s", session.session_id, request_id)
    except asyncio.CancelledError:
        debug_trace.finalize(status="cancelled")
        _log.info("request task cancelled: session=%s request=%s", session.session_id, request_id)
    except Exception as exc:  # noqa: BLE001
        debug_trace.finalize(status=f"error: {type(exc).__name__}")
        _log.error("orchestrator error:\n%s", traceback.format_exc())
        emit(ErrorEvent(
            code="orchestrator_error",
            message=_friendly_error_message(exc),
            request_id=request_id,
        ))
    finally:
        request_store.finish(request_id)
        queue.put_nowait(None)
