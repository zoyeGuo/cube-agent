"""Context variables shared between orchestrator and tools."""
from contextvars import ContextVar

current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
