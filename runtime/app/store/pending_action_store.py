# -*- coding: utf-8 -*-
"""Per-session pending actions that resume after confirmation without re-parsing user text."""
import asyncio
import logging
from dataclasses import dataclass

from app.models.pending_action import PendingAction

_log = logging.getLogger(__name__)


@dataclass
class PendingActionEntry:
    action: PendingAction
    future: asyncio.Future


class PendingActionStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingActionEntry] = {}

    def create(self, session_id: str, action: PendingAction) -> PendingAction:
        self.cancel(session_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[session_id] = PendingActionEntry(action=action, future=fut)
        _log.info("pending action created: session=%s action=%s intent=%s", session_id, action.action_id, action.intent)
        return action

    def current(self, session_id: str) -> PendingAction | None:
        entry = self._pending.get(session_id)
        return entry.action if entry else None

    def resolve(self, session_id: str, decision: str) -> bool:
        entry = self._pending.get(session_id)
        if not entry or entry.future.done():
            return False
        entry.future.set_result(decision)
        _log.info("pending action resolved: session=%s action=%s decision=%s", session_id, entry.action.action_id, decision)
        return True

    def cancel(self, session_id: str) -> bool:
        entry = self._pending.pop(session_id, None)
        if not entry:
            return False
        if not entry.future.done():
            entry.future.cancel()
        _log.info("pending action cancelled: session=%s action=%s", session_id, entry.action.action_id)
        return True

    async def wait(self, session_id: str, timeout: float = 300.0) -> tuple[PendingAction | None, str | None]:
        entry = self._pending.get(session_id)
        if not entry:
            return None, None
        try:
            decision = await asyncio.wait_for(asyncio.shield(entry.future), timeout=timeout)
            return entry.action, decision
        except asyncio.CancelledError:
            _log.info("pending action wait cancelled: session=%s action=%s", session_id, entry.action.action_id)
            return entry.action, None
        except asyncio.TimeoutError:
            _log.info("pending action timeout: session=%s action=%s", session_id, entry.action.action_id)
            return entry.action, None
        finally:
            current = self._pending.get(session_id)
            if current is entry:
                self._pending.pop(session_id, None)


pending_action_store = PendingActionStore()
