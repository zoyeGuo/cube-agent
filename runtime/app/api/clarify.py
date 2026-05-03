# -*- coding: utf-8 -*-
"""POST /v1/clarify — deliver user's answer to a paused orchestrator."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.store.clarification_store import clarification_store

router = APIRouter()


class ClarifyRequest(BaseModel):
    session_id: str
    answer: str


@router.post("/clarify")
async def submit_clarification(body: ClarifyRequest):
    delivered = clarification_store.resolve(body.session_id, body.answer)
    return {"ok": delivered}
