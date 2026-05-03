"""SSE event payload schemas."""
from typing import Literal
from pydantic import BaseModel


class StateEvent(BaseModel):
    type: Literal["state"] = "state"
    name: str
    scope: Literal["cognition", "execution", "system"]


class SpeechEvent(BaseModel):
    type: Literal["speech"] = "speech"
    text: str
    chunk: bool = False  # True when this is a streaming partial


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    request_id: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    request_id: str


class AudioEvent(BaseModel):
    type: Literal["audio"] = "audio"
    data: str   # base64 encoded MP3
    format: str = "mp3"


class SessionEvent(BaseModel):
    type: Literal["session"] = "session"
    session_id: str
    request_id: str | None = None


class ChoiceItem(BaseModel):
    id: str
    label: str
    tag: str = ""
    recommended: bool = False


class ChoiceEvent(BaseModel):
    type: Literal["choice"] = "choice"
    choice_id: str
    title: str
    items: list[ChoiceItem]
    extra_items: list[ChoiceItem] = []
    current_id: str | None = None   # currently selected item, for highlight


class ClarificationEvent(BaseModel):
    type: Literal["clarification"] = "clarification"
    question: str


class ReminderItem(BaseModel):
    id: str
    message: str
    run_time: str | None = None


class ScheduleEvent(BaseModel):
    type: Literal["schedule"] = "schedule"
    reminders: list[ReminderItem]


AnyEvent = SessionEvent | StateEvent | SpeechEvent | AudioEvent | DoneEvent | ErrorEvent | ClarificationEvent
