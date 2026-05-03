# -*- coding: utf-8 -*-
"""Active request registry — supports cooperative cancel by request_id/session_id."""
import asyncio
import logging

_log = logging.getLogger(__name__)


class RequestStore:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._request_to_session: dict[str, str] = {}
        self._session_to_request: dict[str, str] = {}
        self._cancelled: set[str] = set()

    def register(self, request_id: str, session_id: str, task: asyncio.Task) -> None:
        self._tasks[request_id] = task
        self._request_to_session[request_id] = session_id
        self._session_to_request[session_id] = request_id
        _log.info("request registered: request=%s session=%s", request_id, session_id)

    def current_request_id(self, session_id: str) -> str | None:
        return self._session_to_request.get(session_id)

    def session_id_for(self, request_id: str) -> str | None:
        return self._request_to_session.get(request_id)

    def is_cancelled(self, request_id: str) -> bool:
        return request_id in self._cancelled

    def cancel(self, *, request_id: str | None = None, session_id: str | None = None) -> bool:
        resolved_request_id = request_id
        if not resolved_request_id and session_id:
            resolved_request_id = self._session_to_request.get(session_id)
        if not resolved_request_id:
            return False

        resolved_session_id = self._request_to_session.get(resolved_request_id)
        self._cancelled.add(resolved_request_id)

        task = self._tasks.pop(resolved_request_id, None)
        self._request_to_session.pop(resolved_request_id, None)
        if resolved_session_id and self._session_to_request.get(resolved_session_id) == resolved_request_id:
            self._session_to_request.pop(resolved_session_id, None)

        if task and not task.done():
            task.cancel()
            _log.info("request cancel signalled: request=%s session=%s", resolved_request_id, resolved_session_id)
            return True

        _log.info("request marked cancelled without active task: request=%s session=%s", resolved_request_id, resolved_session_id)
        return False

    def finish(self, request_id: str) -> None:
        session_id = self._request_to_session.pop(request_id, None)
        self._tasks.pop(request_id, None)
        self._cancelled.discard(request_id)
        if session_id and self._session_to_request.get(session_id) == request_id:
            self._session_to_request.pop(session_id, None)
        _log.info("request finished: request=%s session=%s", request_id, session_id)


request_store = RequestStore()
