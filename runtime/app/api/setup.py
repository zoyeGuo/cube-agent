"""Setup API — choice submission and soul configuration."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.memory.soul import soul_manager
from app.store.clarification_store import clarification_store
from app.store.pending_action_store import pending_action_store

router = APIRouter()


class ChoiceRequest(BaseModel):
    choice_id: str
    selected_id: str
    selected_label: str = ""
    session_id: str | None = None


@router.post("/choice")
async def submit_choice(req: ChoiceRequest) -> dict:
    delivered = False
    if req.choice_id == "voice_selection":
        soul_manager.update_voice(req.selected_id, req.selected_label)
        delivered = True
    elif req.choice_id.startswith("confirm_") and req.session_id:
        delivered = pending_action_store.resolve(req.session_id, req.selected_id)
        if not delivered:
            delivered = clarification_store.resolve(req.session_id, req.selected_id)
    return {
        "ok": True,
        "choice_id": req.choice_id,
        "selected": req.selected_id,
        "delivered": delivered,
    }


@router.get("/soul")
async def get_soul() -> dict:
    return {
        "exists": soul_manager.exists(),
        "name": soul_manager.get_name(),
        "voice_id": soul_manager.get_voice_id(),
    }
