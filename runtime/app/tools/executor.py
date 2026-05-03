"""Concurrent tool executor — asyncio.gather over registered tools."""
import asyncio
import inspect
import logging
import re
from pathlib import Path
from typing import Any

from app.tools.registry import tool_registry

logger = logging.getLogger(__name__)
_TOOL_TIMEOUT_SECONDS = 20
_ASK_USER_SENTINEL = "__ASK_USER__:"
_CONFIRM_SENTINEL = "__CONFIRM__:"


def _accepts_confirmed(func: Any) -> bool:
    try:
        return "confirmed" in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _python_has_side_effects(code: str) -> bool:
    patterns = (
        r"\bopen\s*\(",
        r"\.write_text\s*\(",
        r"\.write_bytes\s*\(",
        r"\.mkdir\s*\(",
        r"\bunlink\s*\(",
        r"\brename\s*\(",
        r"\breplace\s*\(",
        r"\bos\.remove\s*\(",
        r"\bos\.rename\s*\(",
        r"\bos\.makedirs\s*\(",
        r"\bshutil\.",
    )
    return any(re.search(pattern, code) for pattern in patterns)


def _normalize_desktop_paths_in_code(code: str) -> str:
    desktop = str((Path.home() / "Desktop").resolve()).replace("\\", "/")
    normalized = re.sub(r"/Users/[^/\"'\s]+/Desktop", desktop, code)
    normalized = normalized.replace('"桌面/', f'"{desktop}/')
    normalized = normalized.replace("'桌面/", f"'{desktop}/")
    return normalized


def _needs_confirmation(tc: Any) -> bool:
    args = dict(getattr(tc, "arguments", {}) or {})
    confirmed = bool(args.get("confirmed"))
    if tc.name in {"write_file", "edit_file", "delete_file", "open_file", "run_command"}:
        return not confirmed
    if tc.name == "execute_python":
        code = str(args.get("code", ""))
        return _python_has_side_effects(code) and not confirmed
    return False


def _confirmation_question(tc: Any) -> str:
    args = dict(getattr(tc, "arguments", {}) or {})
    if tc.name == "write_file":
        path = args.get("path", "(未知路径)")
        mode = "追加" if args.get("mode") == "append" else "创建/覆盖"
        preview = _clip(args.get("content", ""), 180)
        return (
            f"{_CONFIRM_SENTINEL}准备在 {path} {mode}一个文本文件。"
            f"内容预览：{preview}"
        )
    if tc.name == "edit_file":
        path = args.get("path", "(未知路径)")
        preview = _clip(args.get("new_content", ""), 180)
        return (
            f"{_CONFIRM_SENTINEL}准备修改 {path}。"
            f"新内容预览：{preview}"
        )
    if tc.name == "open_file":
        path = args.get("path", "(未知目标)")
        return (
            f"{_CONFIRM_SENTINEL}准备打开 {path}"
        )
    if tc.name == "delete_file":
        path = args.get("path", "(未知路径)")
        return (
            f"{_CONFIRM_SENTINEL}准备删除 {path}"
        )
    if tc.name == "execute_python":
        preview = _clip(args.get("code", ""), 180)
        return (
            f"{_CONFIRM_SENTINEL}准备执行一段可能修改本地文件的 Python 代码。"
            f"代码预览：{preview}"
        )
    if tc.name == "run_command":
        command = _clip(args.get("command", ""), 180)
        cwd = _clip(args.get("cwd", ""), 80)
        if cwd:
            return f"{_CONFIRM_SENTINEL}准备在 {cwd} 执行命令：{command}"
        return f"{_CONFIRM_SENTINEL}准备执行命令：{command}"
    return f"{_CONFIRM_SENTINEL}准备执行 {tc.name}"


async def run_tools(tool_calls: list[Any]) -> list[str]:
    """Execute all tool calls concurrently, return results in original order."""
    return list(await asyncio.gather(*[run_tool_call(tc) for tc in tool_calls]))


async def run_tool_call(
    tc: Any,
    *,
    force_confirmed: bool = False,
    bypass_confirmation: bool = False,
) -> str:
    entry = tool_registry.get(tc.name)
    if not entry:
        return f"错误：工具 '{tc.name}' 不存在"
    call_args = dict(getattr(tc, "arguments", {}) or {})

    if force_confirmed and _accepts_confirmed(entry.func):
        call_args["confirmed"] = True
    if tc.name == "execute_python" and "code" in call_args:
        call_args["code"] = _normalize_desktop_paths_in_code(str(call_args["code"]))

    if not bypass_confirmation:
        probe = type("ToolCallProbe", (), {"name": tc.name, "arguments": call_args})()
        if _needs_confirmation(probe):
            question = _confirmation_question(probe)
            logger.info("tool %s requires confirmation", tc.name)
            return question

    try:
        if inspect.iscoroutinefunction(entry.func):
            result = await asyncio.wait_for(
                entry.func(**call_args),
                timeout=_TOOL_TIMEOUT_SECONDS,
            )
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(entry.func, **call_args),
                timeout=_TOOL_TIMEOUT_SECONDS,
            )
        logger.info("tool %s → %s", tc.name, str(result)[:80])
        return str(result)
    except asyncio.TimeoutError:
        logger.warning("tool %s timed out after %ss", tc.name, _TOOL_TIMEOUT_SECONDS)
        return f"执行超时（>{_TOOL_TIMEOUT_SECONDS}s）"
    except Exception as e:  # noqa: BLE001
        logger.warning("tool %s failed: %s", tc.name, e)
        return f"工具执行错误：{e}"


async def _run_one(tc: Any) -> str:
    return await run_tool_call(tc)
