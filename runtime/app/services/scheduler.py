"""APScheduler — async scheduler for proactive reminders and periodic tasks."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

_log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


def start() -> None:
    if not scheduler.running:
        scheduler.start()
        _log.info("scheduler started")


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        _log.info("scheduler stopped")


async def fire_reminder(session_id: str, message: str) -> None:
    """Push a reminder event (text + TTS audio) to the session via WebSocket."""
    import base64
    from app.api.ws import push
    from app.services.tts_service import text_to_speech

    audio_b64: str | None = None
    try:
        audio_bytes = await text_to_speech(message)
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode()
    except Exception as e:
        _log.warning("reminder TTS failed: %s", e)

    payload: dict = {"type": "proactive", "text": message}
    if audio_b64:
        payload["audio"] = audio_b64

    delivered = await push(session_id, payload)
    if not delivered:
        _log.info("reminder not delivered (session offline): %s → %s", session_id, message)


def list_reminders(session_id: str) -> list[dict]:
    """Return pending reminders for a session, sorted by run time."""
    result = []
    for job in scheduler.get_jobs():
        args = getattr(job, "args", None) or []
        if args and args[0] == session_id:
            run_time = job.next_run_time
            result.append({
                "id": job.id,
                "message": args[1] if len(args) > 1 else "",
                "run_time": run_time.isoformat() if run_time else None,
            })
    return sorted(result, key=lambda r: r["run_time"] or "")
