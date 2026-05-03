"""SQLite-backed session store — persists across restarts."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.core.think import strip_think
from app.models.session import Session

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._cache: dict[str, Session] = {}
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id         TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    ts         TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_msg_session
                    ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS action_events (
                    id                   TEXT PRIMARY KEY,
                    session_id           TEXT NOT NULL REFERENCES sessions(id),
                    ts_utc               TEXT NOT NULL,
                    local_date           TEXT NOT NULL,
                    local_time           TEXT NOT NULL,
                    intent               TEXT NOT NULL,
                    user_request         TEXT NOT NULL,
                    status               TEXT NOT NULL,
                    summary              TEXT NOT NULL,
                    expected_effect_json TEXT NOT NULL DEFAULT '{}',
                    observed_effect_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_action_events_ts
                    ON action_events(ts_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_action_events_local_date
                    ON action_events(local_date, ts_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_action_events_session
                    ON action_events(session_id, ts_utc DESC);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "summary" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT NOT NULL DEFAULT ''")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id and session_id in self._cache:
            return self._cache[session_id]

        if session_id:
            session = self._load(session_id)
            if session:
                self._cache[session_id] = session
                return session

        sid = session_id or str(uuid.uuid4())
        now = _now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                (sid, now, now),
            )
        session = Session(session_id=sid)
        self._cache[sid] = session
        return session

    def persist_message(self, session_id: str, role: str, content: str) -> None:
        now = _now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (now, session_id),
            )

    def compact_history(
        self,
        session_id: str,
        summary: str,
        history: list[dict[str, str]],
    ) -> None:
        cleaned_summary = _clean_summary(summary)
        now = _now()
        rows = [
            (session_id, message["role"], message["content"], now)
            for message in history
        ]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            if rows:
                conn.executemany(
                    "INSERT INTO messages (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
                    rows,
                )
            conn.execute(
                "UPDATE sessions SET summary=?, updated_at=? WHERE id=?",
                (cleaned_summary, now, session_id),
            )
        cached = self._cache.get(session_id)
        if cached:
            cached.summary = cleaned_summary
            cached.history = list(history)
            cached.turn_count = sum(1 for message in history if message["role"] == "assistant")

    def persist_action_event(
        self,
        *,
        session_id: str,
        intent: str,
        user_request: str,
        status: str,
        summary: str,
        expected_effect: dict | None = None,
        observed_effect: dict | None = None,
        action_id: str | None = None,
    ) -> str:
        now_utc = datetime.now(timezone.utc)
        ts_utc = now_utc.isoformat()
        local_dt = now_utc.astimezone(_LOCAL_TZ)
        event_id = action_id or f"evt_{uuid.uuid4().hex[:12]}"
        cleaned_request = " ".join((user_request or "").split())
        cleaned_summary = _clean_preview(summary, limit=240)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
                (session_id, ts_utc, ts_utc),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO action_events (
                    id, session_id, ts_utc, local_date, local_time,
                    intent, user_request, status, summary,
                    expected_effect_json, observed_effect_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    ts_utc,
                    local_dt.strftime("%Y-%m-%d"),
                    local_dt.strftime("%H:%M"),
                    intent,
                    cleaned_request,
                    status,
                    cleaned_summary,
                    json.dumps(expected_effect or {}, ensure_ascii=False),
                    json.dumps(observed_effect or {}, ensure_ascii=False),
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (ts_utc, session_id),
            )
        return event_id

    def list_action_events(
        self,
        *,
        start_utc: str | None = None,
        end_utc: str | None = None,
        statuses: list[str] | tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict[str, str | dict]]:
        clauses: list[str] = []
        params: list[object] = []
        if start_utc:
            clauses.append("ts_utc >= ?")
            params.append(start_utc)
        if end_utc:
            clauses.append("ts_utc < ?")
            params.append(end_utc)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    id, session_id, ts_utc, local_date, local_time,
                    intent, user_request, status, summary,
                    expected_effect_json, observed_effect_json
                FROM action_events
                {where_sql}
                ORDER BY ts_utc DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        items: list[dict[str, str | dict]] = []
        for row in rows:
            items.append({
                "id": row["id"],
                "session_id": row["session_id"],
                "ts_utc": row["ts_utc"],
                "local_date": row["local_date"],
                "local_time": row["local_time"],
                "intent": row["intent"],
                "user_request": row["user_request"],
                "status": row["status"],
                "summary": row["summary"],
                "expected_effect": _loads_json(row["expected_effect_json"]),
                "observed_effect": _loads_json(row["observed_effect_json"]),
            })
        return items

    def list_legacy_user_requests(
        self,
        *,
        start_utc: str | None = None,
        end_utc: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, str]]:
        clauses = ["role = 'user'"]
        params: list[object] = []
        if start_utc:
            clauses.append("ts >= ?")
            params.append(start_utc)
        if end_utc:
            clauses.append("ts < ?")
            params.append(end_utc)
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT session_id, content, ts
                FROM messages
                WHERE {' AND '.join(clauses)}
                ORDER BY ts DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "session_id": row["session_id"],
                "content": row["content"],
                "ts": row["ts"],
            }
            for row in rows
        ]

    def recent_identity_context(
        self,
        *,
        summary_limit: int = 6,
        message_limit: int = 24,
    ) -> str:
        with sqlite3.connect(self.db_path) as conn:
            summary_rows = conn.execute(
                """
                SELECT COALESCE(summary, '')
                FROM sessions
                WHERE COALESCE(summary, '') != ''
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (summary_limit,),
            ).fetchall()
            message_rows = conn.execute(
                """
                SELECT role, content
                FROM messages
                ORDER BY id DESC
                LIMIT ?
                """,
                (message_limit,),
            ).fetchall()

        parts: list[str] = []
        for (summary,) in summary_rows:
            cleaned = _clean_summary(summary)
            if cleaned:
                parts.append(cleaned)
        for role, content in reversed(message_rows):
            parts.append(f"{role}: {content}")
        return "\n".join(parts)

    def recent_messages(self, limit: int = 20) -> list[dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT session_id, role, content, ts
                FROM messages
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "ts": ts,
            }
            for session_id, role, content, ts in reversed(rows)
        ]

    def list_sessions(self, limit: int = 20) -> list[dict[str, str | int]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    s.id,
                    s.created_at,
                    s.updated_at,
                    COALESCE(s.summary, '') AS summary,
                    (
                        SELECT COUNT(*)
                        FROM messages m
                        WHERE m.session_id = s.id
                    ) AS message_count,
                    COALESCE((
                        SELECT content
                        FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'assistant'
                        ORDER BY m.id DESC
                        LIMIT 1
                    ), '') AS last_assistant,
                    COALESCE((
                        SELECT content
                        FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'user'
                        ORDER BY m.id DESC
                        LIMIT 1
                    ), '') AS last_user
                FROM sessions s
                WHERE EXISTS (
                    SELECT 1 FROM messages m WHERE m.session_id = s.id
                )
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        items: list[dict[str, str | int]] = []
        for row in rows:
            summary = _clean_summary(row["summary"])
            last_assistant = _clean_preview(row["last_assistant"])
            last_user = _clean_preview(row["last_user"])
            preview = summary or last_assistant or last_user
            title = _build_session_title(summary, last_user, last_assistant)
            items.append({
                "id": row["id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "summary": summary,
                "preview": preview,
                "title": title,
                "message_count": row["message_count"],
            })
        return items

    def get_session_snapshot(self, session_id: str, limit: int = 12) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT id, created_at, updated_at, COALESCE(summary, '') AS summary
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            message_rows = conn.execute(
                """
                SELECT role, content, ts
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        messages = [
            {
                "role": item["role"],
                "content": _clean_preview(item["content"], limit=240),
                "ts": item["ts"],
            }
            for item in reversed(message_rows)
        ]
        last_assistant = next(
            (message["content"] for message in reversed(messages) if message["role"] == "assistant"),
            "",
        )
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "summary": _clean_summary(row["summary"]),
            "messages": messages,
            "last_assistant": last_assistant,
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _load(self, session_id: str) -> Session | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, COALESCE(summary, '') FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not row:
                return None
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()

        session = Session(session_id=session_id, summary=_clean_summary(row[1] or ""))
        for role, content in rows:
            session.history.append({"role": role, "content": content})
            if role == "assistant":
                session.turn_count += 1
        return session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_summary(text: str) -> str:
    return strip_think(text or "").strip()


def _clean_preview(text: str, limit: int = 120) -> str:
    cleaned = " ".join(strip_think(text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _build_session_title(summary: str, last_user: str, last_assistant: str) -> str:
    base = summary or last_user or last_assistant or "未命名会话"
    return _clean_preview(base, limit=28) or "未命名会话"


def _loads_json(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


session_store = SessionStore(Path(settings.sessions_db))
