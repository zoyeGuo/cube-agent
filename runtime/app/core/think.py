"""Utilities for handling <think>...</think> scratchpad blocks."""
import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# System prompt addition — injected into every non-onboarding turn
THINK_INSTRUCTION = (
    "对于复杂任务（需要多个工具、多个步骤或逻辑推导），"
    "先在 <think>...</think> 标签内写出分析和计划，再开始执行。"
    "简单问答无需 <think> 块。"
    "<think> 内容不会展示给用户，可以自由思考。"
)


def strip_think(text: str) -> str:
    """Remove all <think>...</think> blocks and clean up extra whitespace."""
    return _THINK_RE.sub("", text).strip()


def has_think(text: str) -> bool:
    return bool(_THINK_RE.search(text))
