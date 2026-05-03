"""In-memory session model."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    session_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0  # increments after each assistant reply
    summary: str = ""

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        self.turn_count += 1

    def messages(self) -> list[dict[str, Any]]:
        return list(self.history)

    def replace_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = list(messages)
