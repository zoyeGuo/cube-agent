"""Session APIs — list recent sessions and fetch a session snapshot for restore."""
from fastapi import APIRouter, HTTPException, Query

from app.store.session_store import session_store

router = APIRouter()


@router.get("/sessions")
async def list_sessions(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    return {"items": session_store.list_sessions(limit=limit)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, limit: int = Query(default=12, ge=1, le=100)) -> dict:
    snapshot = session_store.get_session_snapshot(session_id, limit=limit)
    if not snapshot:
        raise HTTPException(status_code=404, detail="session not found")
    return snapshot
