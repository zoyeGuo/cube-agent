"""POST /v1/chat — SSE streaming endpoint."""
import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest
from app.store.clarification_store import clarification_store
from app.store.pending_action_store import pending_action_store
from app.store.session_store import session_store
from app.store.request_store import request_store
from app.core import orchestrator

router = APIRouter()


async def _event_stream(session_id: str | None, message: str) -> AsyncIterator[str]:
    session = session_store.get_or_create(session_id)
    previous_request_id = request_store.current_request_id(session.session_id)
    if previous_request_id:
        request_store.cancel(request_id=previous_request_id)
        clarification_store.cancel(session.session_id)
        pending_action_store.cancel(session.session_id)
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    request_id = str(uuid.uuid4())
    task = asyncio.create_task(orchestrator.run(session, message, queue, request_id=request_id))
    request_store.register(request_id, session.session_id, task)
    completed_normally = False

    try:
        while True:
            item = await queue.get()
            if item is None:
                completed_normally = True
                break
            yield item
    finally:
        if not completed_normally and not task.done():
            request_store.cancel(request_id=request_id)


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(req.session_id, req.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
