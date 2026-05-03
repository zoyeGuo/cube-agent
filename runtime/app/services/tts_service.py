"""MiniMax Speech 2.8 HD TTS service."""
import os
import re
import httpx
from app.config import settings


def _clean(text: str) -> str:
    """Strip content that sounds bad when spoken aloud."""
    # URLs
    text = re.sub(r'https?://\S+', '', text)
    # Markdown: code blocks first (multi-line)
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Inline code
    text = re.sub(r'`[^`]+`', '', text)
    # Bold / italic
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    # Headers
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Links → keep label
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # List / blockquote markers
    text = re.sub(r'^[\-*+>]\s+', '', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[-*_]{3,}$', '', text, flags=re.MULTILINE)
    # Emoji (common Unicode ranges)
    text = re.sub(
        r'[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0001F300-\U0001F9FF]',
        '', text,
    )
    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def text_to_speech(text: str) -> bytes | None:
    """Convert text to MP3 bytes. Returns None on failure."""
    from app.memory.soul import soul_manager
    text = _clean(text)[:settings.tts_max_chars]
    if not text:
        return None

    raw_voice = soul_manager.get_voice_id()
    voice_id = raw_voice if raw_voice and raw_voice != "pending" else settings.tts_voice_id

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
    async with httpx.AsyncClient(timeout=30, proxy=proxy, trust_env=False) as client:
        resp = await client.post(
            f"{settings.tts_base_url}/t2a_v2",
            headers={"Authorization": f"Bearer {settings.minimax_api_key}"},
            json={
                "model": settings.tts_model,
                "text": text,
                "voice_setting": {"voice_id": voice_id},
                "audio_setting": {"format": "mp3", "sample_rate": 24000},
            },
        )
        body = resp.json()
        resp.raise_for_status()
        hex_audio: str = body.get("data", {}).get("audio", "")
        return bytes.fromhex(hex_audio) if hex_audio else None
