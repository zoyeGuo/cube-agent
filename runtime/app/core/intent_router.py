"""Intent router — only decides whether a user message is an actionable side-effect request."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Literal

from app.core.model_adapter import call_once

ActionIntent = Literal["write_file", "edit_file", "delete_file", "set_reminder", "open_file", "run_command"]

_ROUTER_SYSTEM = """
你是一个动作意图分类器，只输出 JSON。
请判断用户消息是否属于以下动作之一：
- write_file
- edit_file
- delete_file
- set_reminder
- open_file
- run_command
- none

输出格式固定为：
{"intent":"none|write_file|edit_file|delete_file|set_reminder|open_file|run_command","confidence":0.0}

规则：
- 只做分类，不提取参数
- 没把握就返回 none
- 只输出 JSON，不要解释
""".strip()


@dataclass
class IntentRoute:
    intent: ActionIntent
    source: Literal["rule", "model", "pending"] = "rule"
    confidence: float = 1.0


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    candidates = [text]
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _rule_intent(text: str) -> ActionIntent | None:
    lowered = text.lower()
    command_starters = (
        "ls", "pwd", "cd", "git", "npm", "pnpm", "yarn", "node", "python", "python3",
        "uvicorn", "pip", "rg", "cat", "mkdir", "touch", "echo", "cp", "mv", "find",
        "ps", "lsof", "curl", "bash", "sh", "zsh", "osascript",
    )
    command_regex = "|".join(re.escape(starter) for starter in command_starters)

    if (
        "命令" in text
        or "终端" in text
        or "shell" in lowered
        or "`" in text
        or "cli" in lowered
        or re.search(rf"(?:执行|运行)(?:命令)?\s+(?:{command_regex})(?:\s|$)", lowered)
        or any(lowered.strip().startswith(starter + " ") or lowered.strip() == starter for starter in command_starters)
    ):
        return "run_command"

    if "提醒" in text and any(token in text for token in ("分钟", "小时", "今天", "明天", "后天", "晚上", "早上", "下午", "点")):
        return "set_reminder"

    if (
        ("桌面" in text or "desktop" in lowered)
        and any(token in text for token in ("修改", "改成", "改为", "替换"))
        and "." in text
    ):
        return "edit_file"

    if ("桌面" in text or "desktop" in lowered) and any(token in text for token in ("删除", "删掉", "移除", "去掉")) and "." in text:
        return "delete_file"

    if ("桌面" in text or "desktop" in lowered) and any(token in text for token in ("创建", "新建", "生成", "写")) and "." in text:
        return "write_file"

    if any(token in text for token in ("打开", "启动", "运行")):
        return "open_file"

    return None


def _looks_action_like(text: str) -> bool:
    hints = (
        "创建", "新建", "写", "修改", "改成", "改为", "替换", "删除", "删掉", "提醒", "打开", "启动", "运行",
        "桌面", "desktop", ".txt", ".md", ".json", ".py", "文件", "应用",
        "命令", "终端", "shell", "cli", "git ", "npm ", "python ", "uvicorn ",
    )
    lowered = text.lower()
    return any(h.lower() in lowered for h in hints)


async def _route_with_model(text: str) -> IntentRoute | None:
    raw = await call_once(
        messages=[{"role": "user", "content": text}],
        system=_ROUTER_SYSTEM,
        max_tokens=120,
    )
    data = _extract_json_object(raw)
    if not data:
        return None
    intent = data.get("intent")
    if intent not in {"write_file", "edit_file", "delete_file", "set_reminder", "open_file", "run_command"}:
        return None
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return IntentRoute(intent=intent, source="model", confidence=confidence)


async def classify_action_intent(text: str) -> IntentRoute | None:
    intent = _rule_intent(text)
    if intent:
        return IntentRoute(intent=intent, source="rule", confidence=1.0)
    if not _looks_action_like(text):
        return None
    return await _route_with_model(text)
