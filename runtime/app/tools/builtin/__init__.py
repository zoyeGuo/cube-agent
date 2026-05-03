"""Import all builtins to trigger @tool registration."""
from app.tools.builtin import (
    datetime_tool, calculator_tool, search_tool,
    system_tool, voice_tool, reminder_tool,
    file_tool, code_tool, command_tool, clarify_tool, schedule_tool, memory_tool, architecture_tool,
)

__all__ = [
    "datetime_tool", "calculator_tool", "search_tool",
    "system_tool", "voice_tool", "reminder_tool",
    "file_tool", "code_tool", "command_tool", "clarify_tool", "schedule_tool", "memory_tool", "architecture_tool",
]
