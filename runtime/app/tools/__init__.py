"""Tools package — import this to load all built-in tools."""
from app.tools.registry import tool_registry, tool
import app.tools.builtin  # triggers @tool registration for all builtins

__all__ = ["tool_registry", "tool"]
