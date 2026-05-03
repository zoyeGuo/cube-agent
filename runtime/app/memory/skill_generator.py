# -*- coding: utf-8 -*-
"""Background skill generation — runs after complex tool-use turns."""
import json
import logging
import re
from typing import Any

from app.core.model_adapter import call_once

_log = logging.getLogger(__name__)

_SYSTEM = "你是一个技能提取系统，只输出 JSON，不输出任何其他内容。"

_PROMPT = """分析下面这次工具调用过程，判断是否值得生成一个可复用的技能文档。

用户需求：{user_message}

调用的工具：{tools_called}

工具执行摘要：
{tool_summary}

最终回复：{final_reply}

已有技能列表（避免重复）：
{existing_titles}

判断标准：
- 值得生成：需要多个步骤、有通用性、下次遇到类似任务可直接参考
- 不值得生成：单步查询（查时间/计算）、一次性任务（设置某个具体提醒）、闲聊

如果值得生成，输出技能文档；如果不值得，输出 {{"skip": true}}。

技能文档格式（JSON）：
{{
  "title": "技能名称（简短动词短语，如'搜索并汇总实时信息'）",
  "description": "一句话描述（≤30字）",
  "content": "## When to Use\\n当用户需要...时。\\n\\n## Procedure\\n1. ...\\n2. ...\\n\\n## Pitfalls\\n- ...\\n\\n## Verification\\n确认...",
  "tags": "关键词1,关键词2,关键词3"
}}"""


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _summarize_tools(messages: list[dict[str, Any]], tools_called: set[str]) -> str:
    lines = []
    for m in messages:
        if m.get("role") == "tool":
            content = m.get("content", "")
            if len(content) > 120:
                content = content[:120] + "..."
            lines.append(f"- 工具结果: {content}")
    return "\n".join(lines) if lines else "（无详细输出）"


async def generate_skill(
    user_message: str,
    tools_called: set[str],
    messages: list[dict[str, Any]],
    final_reply: str,
    skills_manager,
) -> None:
    """Non-blocking: generate or update a skill from a completed tool-use turn."""
    if len(tools_called) < 2:
        return  # 只对 2+ 工具调用生成技能

    existing_titles = skills_manager.all_titles()
    tool_summary = _summarize_tools(messages, tools_called)

    prompt = _PROMPT.format(
        user_message=user_message,
        tools_called=", ".join(sorted(tools_called)),
        tool_summary=tool_summary,
        final_reply=final_reply[:300] if final_reply else "",
        existing_titles="\n".join(f"- {t}" for t in existing_titles) or "（暂无）",
    )

    try:
        raw = await call_once(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            max_tokens=800,
        )
        result = _parse_json(raw)
        if not result or result.get("skip"):
            return

        title = result.get("title", "").strip()
        description = result.get("description", "").strip()
        content = result.get("content", "").strip()
        tags = result.get("tags", "").strip()

        if not title or not content:
            return

        # 若同名技能已存在则更新，否则新建
        existing = skills_manager.find_by_title(title)
        if existing:
            skills_manager.update_skill(existing["id"], content, tags)
        else:
            skills_manager.save_skill(title, description, content, tags)

    except Exception:  # noqa: BLE001
        _log.debug("skill generation failed (non-critical)", exc_info=True)
