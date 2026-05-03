from app.adapters.hermes import HermesClient, MockHermesClient
from app.schemas.chat import ChatRequest, ChatResponse


class ChatService:
    def __init__(self, hermes_client: HermesClient | None = None) -> None:
        self.hermes_client = hermes_client or MockHermesClient()

    def chat(self, payload: ChatRequest) -> ChatResponse:
        hermes_result = self.hermes_client.generate_reply(
            message=payload.message,
            session_id=payload.session_id,
        )
        return ChatResponse(**hermes_result)
