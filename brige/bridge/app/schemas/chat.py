from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message sent from the desktop frontend.")
    session_id: str = Field(..., description="Client-side session identifier.")


class ChatResponse(BaseModel):
    reply: str
    state: str
    session_id: str
    source: str
