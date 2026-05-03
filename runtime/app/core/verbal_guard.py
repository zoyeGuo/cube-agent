"""Guard against claiming side-effect completion without real tool execution."""
from __future__ import annotations

import re

_SIDE_EFFECT_TOOLS = {
    "write_file", "edit_file", "delete_file", "open_file",
    "set_reminder", "run_command", "execute_python",
}
_ACTION_HINTS = (
    "创建", "新建", "写入", "写到", "修改", "改为", "改成", "替换",
    "删除", "删掉", "移除", "提醒", "打开", "启动", "执行", "运行",
    "保存", "下载", "安装", "桌面", "文件", "命令", "终端", "应用",
)
_COMPLETION_PATTERNS = (
    r"已(?:经)?(?:把)?[^。！？\n]{0,24}(?:改为|改成|修改|创建|删除|写入|设置|执行|打开|保存|安装)",
    r"(?:改好了|做好了|完成了|已处理好)",
)
_NON_COMPLETION_PATTERNS = (
    r"未执行", r"没执行", r"未完成", r"没完成", r"没有完成",
    r"不能", r"无法", r"做不到", r"需要确认", r"请确认", r"还没",
)


def _looks_like_action_request(text: str) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in _ACTION_HINTS)


def _looks_like_completion_claim(text: str) -> bool:
    if any(re.search(pattern, text) for pattern in _NON_COMPLETION_PATTERNS):
        return False
    return any(re.search(pattern, text) for pattern in _COMPLETION_PATTERNS)


def detect(user_message: str, assistant_text: str, tools_called: set[str]) -> str | None:
    """Return a correction prompt when the model claims completion without side-effect tools."""
    if not _looks_like_action_request(user_message):
        return None
    if tools_called & _SIDE_EFFECT_TOOLS:
        return None
    if not _looks_like_completion_claim(assistant_text):
        return None
    return (
        "你刚才声称已经完成了一个会改变真实世界状态的操作，但本轮没有任何真实工具执行。"
        "请重新回答：要么调用合适的工具完成它；要么明确说明你还没有完成，"
        "不要口头假装已经执行成功。"
    )
