"""Tool registry — @tool decorator + schema auto-generation."""
import inspect
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints


_TYPE_MAP: dict[type, dict] = {
    str:   {"type": "string"},
    int:   {"type": "integer"},
    float: {"type": "number"},
    bool:  {"type": "boolean"},
}


@dataclass
class ToolEntry:
    name: str
    func: Callable
    schema: dict[str, Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(self, func: Callable) -> Callable:
        name = func.__name__
        doc = (func.__doc__ or "").strip()

        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}

        sig = inspect.signature(func)
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param_name == "confirmed":
                continue
            hint = hints.get(param_name, str)
            properties[param_name] = _TYPE_MAP.get(hint, {"type": "string"})
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        self._tools[name] = ToolEntry(
            name=name,
            func=func,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": doc,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            },
        )
        return func

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def get_schemas(self) -> list[dict[str, Any]]:
        return [e.schema for e in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


tool_registry = ToolRegistry()


def tool(func: Callable) -> Callable:
    """Decorator: register a function as an AI-callable tool."""
    return tool_registry.register(func)
