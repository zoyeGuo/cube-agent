# -*- coding: utf-8 -*-
"""Skills system — agentskills.io-compatible SKILL.md file store."""
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)


def _slugify(text: str) -> str:
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'[^\w\s-]', '', text.lower())
    text = re.sub(r'[\s_-]+', '-', text)
    return text.strip('-')[:64]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_skill_file(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return None

    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None

    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None

    body = raw[m.end():].strip()
    metadata = fm.get('metadata') or {}
    tags_raw = metadata.get('tags') or fm.get('tags') or []
    tags = ','.join(tags_raw) if isinstance(tags_raw, list) else str(tags_raw)

    name = fm.get('name') or path.parent.name
    return {
        'id': name,
        'title': name,
        'description': fm.get('description', ''),
        'content': body,
        'tags': tags,
        'use_count': int(metadata.get('use_count', 0)),
    }


def _write_skill_file(
    path: Path,
    name: str,
    description: str,
    content: str,
    tags: str,
    use_count: int = 0,
    created_at: str | None = None,
) -> None:
    tags_list = [t.strip() for t in tags.split(',') if t.strip()]
    now = _now()
    fm: dict = {
        'name': name,
        'description': description,
        'version': '1.0.0',
        'platforms': ['macos', 'linux', 'windows'],
        'metadata': {
            'tags': tags_list,
            'category': 'general',
            'use_count': use_count,
            'created_at': created_at or now,
            'updated_at': now,
        },
    }
    fm_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    path.write_text(f'---\n{fm_str}---\n\n{content}\n', encoding='utf-8')


class SkillsManager:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _skill_path(self, name: str) -> Path:
        return self.skills_dir / _slugify(name) / 'SKILL.md'

    # ── Write ──────────────────────────────────────────────────────────────────

    def save_skill(self, title: str, description: str, content: str, tags: str) -> str:
        """Write a new skill as SKILL.md. Returns the skill name used as id."""
        slug = _slugify(title)
        skill_dir = self.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_file(skill_dir / 'SKILL.md', title, description, content, tags)
        _log.info('skill saved: %s', slug)
        return title

    def update_skill(self, skill_id: str, content: str, tags: str) -> None:
        path = self._skill_path(skill_id)
        if not path.exists():
            return
        existing = _parse_skill_file(path)
        if not existing:
            return
        _write_skill_file(
            path,
            name=existing['id'],
            description=existing['description'],
            content=content,
            tags=tags,
            use_count=existing['use_count'],
        )
        _log.info('skill updated: %s', skill_id)

    def increment_use(self, skill_id: str) -> None:
        path = self._skill_path(skill_id)
        if not path.exists():
            return
        existing = _parse_skill_file(path)
        if not existing:
            return
        _write_skill_file(
            path,
            name=existing['id'],
            description=existing['description'],
            content=existing['content'],
            tags=existing['tags'],
            use_count=existing['use_count'] + 1,
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def search_skills(self, query: str, limit: int = 3) -> list[dict]:
        """Keyword search across name/description/tags. Falls back to most-used."""
        all_skills = self._load_all()
        if not all_skills:
            return []
        if not query.strip():
            return sorted(all_skills, key=lambda s: s['use_count'], reverse=True)[:limit]

        words = re.findall(r'\w+', query.lower())

        def score(skill: dict) -> int:
            haystack = ' '.join([skill['title'], skill['description'], skill['tags']]).lower()
            return sum(1 for w in words if w in haystack)

        scored = sorted(all_skills, key=lambda s: (-score(s), -s['use_count']))
        matched = [s for s in scored if score(s) > 0]
        return (matched or scored)[:limit]

    def find_by_title(self, title: str) -> dict | None:
        path = self._skill_path(title)
        return _parse_skill_file(path) if path.exists() else None

    def all_titles(self) -> list[str]:
        return [s['title'] for s in self._load_all()]

    def _load_all(self) -> list[dict]:
        skills = []
        for skill_md in self.skills_dir.glob('*/SKILL.md'):
            parsed = _parse_skill_file(skill_md)
            if parsed:
                skills.append(parsed)
        return skills


skills_manager = SkillsManager(
    Path.home() / '.secretary' / 'skills'
)
