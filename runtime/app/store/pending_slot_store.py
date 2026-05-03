# -*- coding: utf-8 -*-
"""Per-session pending slot clarification context for action continuation across turns."""
import logging
from dataclasses import dataclass

from app.core.intent_router import ActionIntent

_log = logging.getLogger(__name__)


@dataclass
class PendingSlotEntry:
    intent: ActionIntent
    original_request: str
    clarification: str


class PendingSlotStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingSlotEntry] = {}

    def set(self, session_id: str, *, intent: ActionIntent, original_request: str, clarification: str) -> PendingSlotEntry:
        entry = PendingSlotEntry(
            intent=intent,
            original_request=original_request,
            clarification=clarification,
        )
        self._pending[session_id] = entry
        _log.info("pending slot created: session=%s intent=%s", session_id, intent)
        return entry

    def get(self, session_id: str) -> PendingSlotEntry | None:
        return self._pending.get(session_id)

    def clear(self, session_id: str) -> bool:
        entry = self._pending.pop(session_id, None)
        if not entry:
            return False
        _log.info("pending slot cleared: session=%s intent=%s", session_id, entry.intent)
        return True


pending_slot_store = PendingSlotStore()

