from abc import ABC, abstractmethod


class HermesClient(ABC):
    @abstractmethod
    def generate_reply(self, message: str, session_id: str) -> dict[str, str]:
        """Return a normalized chat result for the bridge layer."""


class MockHermesClient(HermesClient):
    def generate_reply(self, message: str, session_id: str) -> dict[str, str]:
        return {
            "reply": "你好，我是秘书助手。",
            "state": "done",
            "session_id": session_id,
            "source": "mock",
        }
