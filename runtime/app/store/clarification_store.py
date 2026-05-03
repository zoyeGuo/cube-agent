# -*- coding: utf-8 -*-
"""Per-session clarification futures — pause orchestrator until user answers."""
import asyncio
import logging

_log = logging.getLogger(__name__)


class ClarificationStore:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    def create(self, session_id: str) -> asyncio.Future:
        """Create a future for this session. Replaces any existing pending future."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[session_id] = fut
        return fut

    def resolve(self, session_id: str, answer: str) -> bool:
        """Deliver the user's answer. Returns True if there was a pending question."""
        fut = self._pending.pop(session_id, None)
        if fut and not fut.done():
            fut.set_result(answer)
            _log.info("clarification resolved: session=%s", session_id)
            return True
        return False

    def cancel(self, session_id: str) -> bool:
        """Cancel and remove a pending clarification future."""
        fut = self._pending.pop(session_id, None)
        if fut and not fut.done():
            fut.cancel()
            _log.info("clarification cancelled: session=%s", session_id)
            return True
        return False

    async def wait(self, session_id: str, timeout: float = 300.0) -> str | None:
        """Await user answer. Returns None on timeout."""
        fut = self._pending.get(session_id)
        if not fut:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.CancelledError:
            self._pending.pop(session_id, None)
            _log.info("clarification wait cancelled: session=%s", session_id)
            return None
        except asyncio.TimeoutError:
            self._pending.pop(session_id, None)
            _log.info("clarification timeout: session=%s", session_id)
            return None

    def is_pending(self, session_id: str) -> bool:
        return session_id in self._pending


clarification_store = ClarificationStore()
