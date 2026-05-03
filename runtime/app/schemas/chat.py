"""REST request/response schemas."""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    """Returned only for non-streaming; v0.1 uses SSE so this is a placeholder."""
    session_id: str
    request_id: str
