"""Post-turn memory extraction — runs in the background after each response."""
import json
import logging
import re
from typing import Any

from app.core.model_adapter import call_once
from app.core.think import strip_think
from app.memory.manager import IMPORTANCE_HIGH, IMPORTANCE_NORMAL

_log = logging.getLogger(__name__)

_SYSTEM = "你是一个记忆提取系统，只输出 JSON，不输出任何其他内容。"

_PROMPT = """请分析下面的对话，更新结构化长期记忆，并生成一条情景记忆。

当前 MEMORY 结构：
{memory}

当前 USER 结构：
{user}

最近对话：
{conversation}

规则：
- 只保留有长期价值的信息；一次性查询、寒暄、纯闲聊不要写入 memory / user
- user.identity：用户称呼、身份、与助手关系
- user.preferences：明确偏好、不喜欢、默认选择
- user.habits：交流风格、工作习惯、常用方式
- user.context：稳定背景、长期目标、近期持续状态
- memory.projects：进行中的项目、任务、待办上下文
- memory.decisions：已确认决定、以后默认做法
- memory.facts：长期稳定事实
- memory.constraints：限制、禁忌、注意事项
- 每个数组元素都用一句短句，尽量 8-24 字，去重
- 没有新内容就保留原值；过时内容可以从数组里删除
- episode.summary：用一句话描述本轮发生了什么（含用户意图和结果，≤40字）
- episode.tags：3-6个关键词，逗号分隔，用于日后检索
- episode.importance：重要性评分，2=重要，1=普通
  - 判断为"重要"的情形：用户做出决策或承诺、披露个人重要信息、确认偏好或习惯、解决了重大问题
  - 判断为"普通"的情形：一次性查询（天气/时间/计算）、闲聊、常规问答、无后续价值的交互

请严格按如下 JSON 格式返回：
{{"memory": {{"projects": [], "decisions": [], "facts": [], "constraints": []}}, "user": {{"identity": [], "preferences": [], "habits": [], "context": []}}, "episode": {{"summary": "<本轮摘要>", "tags": "<关键词1,关键词2,...>", "importance": 1}}}}"""

_IMMEDIATE_KEYWORDS = (
    "我叫", "叫我", "称呼我", "你可以叫我", "我的名字", "我名字",
    "你叫", "你就叫", "称呼你", "记住", "记一下", "以后", "下次",
    "默认", "偏好", "喜欢", "不喜欢", "习惯", "尽量", "不要", "别",
    "项目", "任务", "需求", "正在做", "现在在做", "最近在做", "开发",
    "决定", "定了", "确认", "确定", "统一", "改成", "改为", "就用",
    "我是", "职业", "工作是", "公司", "学校",
)

_IMMEDIATE_PATTERNS = (
    re.compile(r"(?:以后|下次|默认).{0,16}(?:用|叫|记|按|改)"),
    re.compile(r"(?:喜欢|不喜欢|偏好|习惯).{0,20}"),
    re.compile(r"(?:正在|最近在|目前在).{0,24}(?:做|开发|写|推进|负责)"),
    re.compile(r"(?:决定|确认|确定).{0,18}(?:用|改|做|保持)"),
)
_META_QUERY_KEYWORDS = (
    "读取记忆", "你记得", "你还记得", "之前让我做过什么", "历史记录", "回忆",
    "让我做什么", "做过什么", "昨天我都让你",
    "我是谁", "你是谁", "项目结构", "系统结构", "代码结构", "架构",
)


def _format_conversation(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        role = "用户" if m["role"] == "user" else "助手"
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _clean_text(text: str) -> str:
    return " ".join(strip_think(text or "").split())


def should_extract_immediately(user_text: str, assistant_text: str = "") -> bool:
    merged = _clean_text(f"{user_text}\n{assistant_text}")
    if not merged:
        return False
    if any(keyword in merged for keyword in _IMMEDIATE_KEYWORDS):
        return True
    return any(pattern.search(merged) for pattern in _IMMEDIATE_PATTERNS)


def _fallback_episode(messages: list[dict[str, Any]]) -> tuple[str, str, int] | None:
    user_text = ""
    assistant_text = ""

    for message in reversed(messages):
        if not user_text and message.get("role") == "user":
            user_text = _clean_text(str(message.get("content") or ""))
        elif not assistant_text and message.get("role") == "assistant":
            assistant_text = _clean_text(str(message.get("content") or ""))
        if user_text and assistant_text:
            break

    if not user_text and not assistant_text:
        return None

    summary_parts = []
    if user_text:
        summary_parts.append(f"用户提到：{user_text[:18]}")
    if assistant_text:
        summary_parts.append(f"助手回应：{assistant_text[:18]}")
    summary = "；".join(summary_parts)[:40]

    tags = []
    lowered = f"{user_text}\n{assistant_text}"
    if "提醒" in lowered:
        tags.append("提醒")
    if "语音" in lowered or "声音" in lowered or "音色" in lowered:
        tags.append("语音")
    if "代码" in lowered or "文件" in lowered:
        tags.append("代码")
    if "名字" in lowered or "称呼" in lowered or "zoye" in lowered or "小Q" in lowered:
        tags.append("身份")

    importance = IMPORTANCE_HIGH if any(token in lowered for token in ("名字", "称呼", "偏好", "风格", "记住")) else IMPORTANCE_NORMAL
    return summary, ",".join(tags[:6]), importance


def _save_fallback_episode(messages: list[dict[str, Any]], memory_manager) -> None:
    episode = _fallback_episode(messages)
    if not episode:
        return
    summary, tags, importance = episode
    memory_manager.save_episode(summary, tags, importance)


def _conversation_slice(messages: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    if len(messages) <= limit:
        return messages
    return messages[-limit:]


def _should_skip_persist(messages: list[dict[str, Any]]) -> bool:
    last_user = next(
        (
            _clean_text(str(message.get("content") or ""))
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    if not last_user:
        return False
    return any(keyword in last_user for keyword in _META_QUERY_KEYWORDS)


async def extract_and_save(
    messages: list[dict[str, Any]],
    memory_manager,
) -> None:
    """Non-blocking: extract memories + episode from messages and persist."""
    if _should_skip_persist(messages):
        return

    snapshot = memory_manager.structured_snapshot()
    existing_memory = snapshot["memory"]
    existing_user = snapshot["user"]
    conversation = _format_conversation(_conversation_slice(messages))

    prompt = _PROMPT.format(
        memory=json.dumps(existing_memory, ensure_ascii=False, indent=2),
        user=json.dumps(existing_user, ensure_ascii=False, indent=2),
        conversation=conversation,
    )

    try:
        raw = await call_once(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            max_tokens=1024,
        )
        result = _parse_json(raw)
        if not result:
            _log.warning("memory extraction returned non-JSON; saving fallback episode")
            _save_fallback_episode(messages, memory_manager)
            return

        new_mem = memory_manager.coerce_memory_sections(result.get("memory"), existing_memory)
        new_usr = memory_manager.coerce_user_sections(result.get("user"), existing_user)
        if any(existing_memory.values()) and not any(new_mem.values()):
            new_mem = existing_memory
        if any(existing_user.values()) and not any(new_usr.values()):
            new_usr = existing_user
        if new_mem != existing_memory:
            memory_manager.save_memory_sections(new_mem)
        if new_usr != existing_user:
            memory_manager.save_user_sections(new_usr)

        ep = result.get("episode")
        if ep and isinstance(ep, dict):
            summary = ep.get("summary", "").strip()
            tags = ep.get("tags", "").strip()
            importance = IMPORTANCE_HIGH if ep.get("importance") == 2 else IMPORTANCE_NORMAL
            if summary:
                memory_manager.save_episode(summary, tags, importance)
            else:
                _save_fallback_episode(messages, memory_manager)
        else:
            _save_fallback_episode(messages, memory_manager)
    except Exception:  # noqa: BLE001
        _log.warning("memory extraction failed; saving fallback episode", exc_info=True)
        _save_fallback_episode(messages, memory_manager)
