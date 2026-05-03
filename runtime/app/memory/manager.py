"""Memory manager — MEMORY.md, USER.md, SQLite episodic store + FTS5."""
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings

_log = logging.getLogger(__name__)

IMPORTANCE_NORMAL = 1
IMPORTANCE_HIGH   = 2

_USER_SECTION_TITLES = {
    "identity": "身份信息",
    "preferences": "偏好设置",
    "habits": "习惯与交流方式",
    "context": "背景与长期上下文",
}

_MEMORY_SECTION_TITLES = {
    "projects": "进行中的事项",
    "decisions": "已确认决定",
    "facts": "长期事实",
    "constraints": "约束与注意事项",
}

_SECTION_ITEM_LIMIT = 8


def _normalize_item(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    cleaned = re.sub(r"^\s*[-*•]+\s*", "", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" \t\r\n-")


def _dedupe_items(items: list[str], limit: int = _SECTION_ITEM_LIMIT) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = _normalize_item(item)
        if not normalized:
            continue
        key = re.sub(r"\s+", "", normalized).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _blank_sections(section_titles: dict[str, str]) -> dict[str, list[str]]:
    return {key: [] for key in section_titles}


def _parse_structured_doc(
    content: str,
    section_titles: dict[str, str],
    *,
    fallback_key: str,
) -> dict[str, list[str]]:
    sections = _blank_sections(section_titles)
    if not content.strip():
        return sections

    heading_to_key = {title: key for key, title in section_titles.items()}
    current_key: str | None = None
    saw_heading = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            current_key = heading_to_key.get(line[3:].strip())
            saw_heading = current_key is not None
            continue
        if line.startswith("#"):
            continue
        if current_key is None:
            if saw_heading:
                continue
            sections[fallback_key].append(line)
            continue
        sections[current_key].append(line)

    return {key: _dedupe_items(value) for key, value in sections.items()}


def _format_structured_doc(
    title: str,
    section_titles: dict[str, str],
    sections: dict[str, list[str]],
) -> str:
    normalized = {
        key: _dedupe_items(sections.get(key, []))
        for key in section_titles
    }
    if not any(normalized.values()):
        return ""

    lines = [f"# {title}", ""]
    for key, heading in section_titles.items():
        items = normalized.get(key, [])
        if not items:
            continue
        lines.append(f"## {heading}")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines).strip()


def _coerce_sections(
    raw: Any,
    section_titles: dict[str, str],
    existing: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    base = {
        key: list((existing or {}).get(key, []))
        for key in section_titles
    }
    if not isinstance(raw, dict):
        return {key: _dedupe_items(value) for key, value in base.items()}

    for key in section_titles:
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, list):
            base[key] = [str(item) for item in value]
        elif isinstance(value, str):
            base[key] = [value]
        elif value is None:
            base[key] = []
    return {key: _dedupe_items(value) for key, value in base.items()}


class MemoryManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = data_dir / "MEMORY.md"
        self.user_file = data_dir / "USER.md"
        self.db_path = data_dir / "state.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         TEXT NOT NULL,
                    summary    TEXT NOT NULL,
                    tags       TEXT NOT NULL DEFAULT '',
                    importance INTEGER NOT NULL DEFAULT 1
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
                    USING fts5(summary, tags, tokenize='unicode61');

                -- 旧 sessions 表保留（兼容）
                CREATE TABLE IF NOT EXISTS sessions (
                    id      TEXT PRIMARY KEY,
                    ts      TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    raw     TEXT NOT NULL
                );
            """)
            # 迁移：旧表若没有 importance 列则补加
            cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
            if "importance" not in cols:
                conn.execute("ALTER TABLE episodes ADD COLUMN importance INTEGER NOT NULL DEFAULT 1")

    # ── Flat files ────────────────────────────────────────────────────────────

    def load_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8").strip() if self.memory_file.exists() else ""

    def load_user(self) -> str:
        return self.user_file.read_text(encoding="utf-8").strip() if self.user_file.exists() else ""

    def _write_text_file(self, path: Path, content: str) -> None:
        cleaned = content.strip()
        if not cleaned:
            if path.exists():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cleaned, encoding="utf-8")

    def save_memory(self, content: str) -> None:
        self._write_text_file(self.memory_file, content)

    def save_user(self, content: str) -> None:
        self._write_text_file(self.user_file, content)

    def memory_sections(self) -> dict[str, list[str]]:
        return _parse_structured_doc(
            self.load_memory(),
            _MEMORY_SECTION_TITLES,
            fallback_key="facts",
        )

    def user_sections(self) -> dict[str, list[str]]:
        return _parse_structured_doc(
            self.load_user(),
            _USER_SECTION_TITLES,
            fallback_key="context",
        )

    def coerce_memory_sections(self, raw: Any, existing: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
        return _coerce_sections(raw, _MEMORY_SECTION_TITLES, existing)

    def coerce_user_sections(self, raw: Any, existing: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
        return _coerce_sections(raw, _USER_SECTION_TITLES, existing)

    def save_memory_sections(self, sections: dict[str, list[str]]) -> None:
        self.save_memory(_format_structured_doc("Memory", _MEMORY_SECTION_TITLES, sections))

    def save_user_sections(self, sections: dict[str, list[str]]) -> None:
        self.save_user(_format_structured_doc("User", _USER_SECTION_TITLES, sections))

    def structured_snapshot(self) -> dict[str, dict[str, list[str]]]:
        return {
            "memory": self.memory_sections(),
            "user": self.user_sections(),
        }

    # ── Episodic memory ───────────────────────────────────────────────────────

    def save_episode(self, summary: str, tags: str = "", importance: int = IMPORTANCE_NORMAL) -> bool:
        cleaned_summary = _normalize_item(summary)
        cleaned_tags = _normalize_item(tags.replace("，", ","))
        if not cleaned_summary:
            return False
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            latest = conn.execute(
                "SELECT summary FROM episodes ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if latest and _normalize_item(str(latest[0])) == cleaned_summary:
                return False
            cur = conn.execute(
                "INSERT INTO episodes (ts, summary, tags, importance) VALUES (?, ?, ?, ?)",
                (ts, cleaned_summary, cleaned_tags, importance),
            )
            conn.execute(
                "INSERT INTO episodes_fts(rowid, summary, tags) VALUES (?, ?, ?)",
                (cur.lastrowid, cleaned_summary, cleaned_tags),
            )
        return True

    def search_episodes(self, query: str, limit: int = 5) -> list[dict]:
        """FTS5 keyword search; falls back to most-recent episodes on no match."""
        if not query.strip():
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """SELECT e.id, e.ts, e.summary, e.importance
                       FROM episodes_fts f
                       JOIN episodes e ON e.id = f.rowid
                       WHERE episodes_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (query, limit),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
            rows = conn.execute(
                "SELECT id, ts, summary, importance FROM episodes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_episodes(self, limit: int = 5) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, ts, summary, tags, importance FROM episodes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def episodes_in_window(
        self,
        *,
        start_utc: str | None = None,
        end_utc: str | None = None,
        limit: int = 8,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        if start_utc:
            clauses.append("ts >= ?")
            params.append(start_utc)
        if end_utc:
            clauses.append("ts < ?")
            params.append(end_utc)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT id, ts, summary, tags, importance
                FROM episodes
                {where_sql}
                ORDER BY ts DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def cleanup_episodes(self, normal_ttl_days: int = 30) -> int:
        """Delete importance=1 episodes older than normal_ttl_days. Returns deleted count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=normal_ttl_days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            # 先从 FTS 表删除对应行
            old_ids = [
                r[0] for r in conn.execute(
                    "SELECT id FROM episodes WHERE importance = ? AND ts < ?",
                    (IMPORTANCE_NORMAL, cutoff),
                ).fetchall()
            ]
            if not old_ids:
                return 0
            placeholders = ",".join("?" * len(old_ids))
            conn.execute(f"DELETE FROM episodes_fts WHERE rowid IN ({placeholders})", old_ids)
            conn.execute(f"DELETE FROM episodes WHERE id IN ({placeholders})", old_ids)
        _log.info("episodic cleanup: removed %d expired normal episodes", len(old_ids))
        return len(old_ids)

    # ── Prompt injection ───────────────────────────────────────────────────────

    def build_system_prompt(
        self,
        soul: str,
        base: str,
        relevant_episodes: list[dict] | None = None,
        relevant_skills: list[dict] | None = None,
    ) -> str:
        """Layer: SOUL → base prompt → MEMORY.md → USER.md → 情景记忆 → 技能."""
        parts = []
        if soul:
            parts.append(soul)
        parts.append(base)
        mem = self.load_memory()
        usr = self.load_user()
        if mem:
            parts.append(f"\n## 记忆\n{mem}")
        if usr:
            parts.append(f"\n## 用户信息\n{usr}")
        if relevant_episodes:
            lines = []
            for ep in relevant_episodes:
                try:
                    dt = datetime.fromisoformat(ep["ts"]).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    dt = ep["ts"][:16]
                mark = " ★" if ep.get("importance") == IMPORTANCE_HIGH else ""
                lines.append(f"- [{dt}]{mark} {ep['summary']}")
            parts.append("\n## 相关情景记忆\n" + "\n".join(lines))
        if relevant_skills:
            skill_blocks = []
            for sk in relevant_skills:
                skill_blocks.append(f"### {sk['title']}\n{sk['content']}")
            parts.append("\n## 可用技能\n以下是过去总结的技能文档，当前任务若与之相关请参考执行步骤：\n\n" + "\n\n".join(skill_blocks))
        return "\n".join(parts)


memory_manager = MemoryManager(Path(settings.memory_dir))
