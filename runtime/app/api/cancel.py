# -*- coding: utf-8 -*-
"""POST /v1/cancel — cancel the active request by request_id/session_id."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.store.clarification_store import clarification_store
from app.store.pending_action_store import pending_action_store
from app.store.request_store import request_store

router = APIRouter()


class CancelRequest(BaseModel):
    request_id: str | None = None
    session_id: str | None = None


@router.post("/cancel")
async def cancel_request(body: CancelRequest):
    if not body.request_id and not body.session_id:
        return {"ok": False, "reason": "missing_request"}

    session_id = body.session_id or (
        request_store.session_id_for(body.request_id) if body.request_id else None
    )
    cancelled = request_store.cancel(request_id=body.request_id, session_id=body.session_id)
    if session_id:
        clarification_store.cancel(session_id)
        pending_action_store.cancel(session_id)
    return {"ok": cancelled}
