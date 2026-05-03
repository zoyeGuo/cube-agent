"""Structured action payloads that survive confirmation and resume deterministically."""
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class PendingActionStep:
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class PendingAction:
    action_id: str
    intent: str
    title: str
    steps: list[PendingActionStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
