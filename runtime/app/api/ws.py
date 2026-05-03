"""WebSocket push channel — persistent connection per session for proactive events."""
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

_log = logging.getLogger(__name__)
router = APIRouter()

# session_id → active WebSocket
_connections: dict[str, WebSocket] = {}


@router.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    _connections[session_id] = websocket
    _log.info("ws connected: %s", session_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        _log.info("ws disconnected: %s", session_id)
    finally:
        _connections.pop(session_id, None)


async def push(session_id: str, event: dict[str, Any]) -> bool:
    """Push a JSON event to a connected session. Returns True if delivered."""
    ws = _connections.get(session_id)
    if not ws:
        return False
    try:
        await ws.send_json(event)
        return True
    except Exception as e:
        _log.warning("ws push failed (%s): %s", session_id, e)
        _connections.pop(session_id, None)
        return False


def connected_sessions() -> list[str]:
    return list(_connections.keys())
