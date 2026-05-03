"""Soul manager — SOUL.md persona file."""
import re
from pathlib import Path

from app.config import settings

DEFAULT_ASSISTANT_NAME = "助手"
DEFAULT_USER_NAME = "用户"
DEFAULT_PERSONALITY = "- 简洁直接\n- 专业友好"

_TEMPLATE = """\
# Soul

## Identity
- Name: {name}
- User: {user_name}

## Personality
{personality}

## Voice
- voice_id: {voice_id}
- voice_name: {voice_name}
"""

ONBOARDING_STEP1 = (
    "这是用户第一次启动，需要完成初始化。简短问候后，问用户三件事："
    "①怎么称呼用户自己；②希望叫你什么名字；③喜欢什么样的交流风格（比如冷静理性、温柔亲切、活泼开朗、毒舌幽默等）。"
    "语气自然，两三句话，不要啰嗦，不要列编号。"
)

ONBOARDING_STEP2 = (
    "用户刚完成了初始设置。简短用用户的名字叫一下他，确认记住了他的偏好，"
    "告诉他正在根据风格匹配音色，马上就好。一两句话即可。"
)

_NAME_CHARS = r"[A-Za-z0-9_\-\u4e00-\u9fff]{1,24}"
_INVALID_NAMES = {"用户", "助手", "我", "你", "自己", "对方", "名字", "称呼"}
_USER_NAME_PATTERNS = [
    re.compile(rf"(?:用户(?:自己)?(?:的)?称呼为|用户叫)[:： ]*({_NAME_CHARS})"),
    re.compile(rf"(?:我叫|叫我|称呼我|你可以叫我|可以叫我|我的名字是|我名字是)[:： ]*({_NAME_CHARS})"),
]
_ASSISTANT_NAME_PATTERNS = [
    re.compile(rf"(?:助手(?:的)?称呼为|助手叫)[:： ]*({_NAME_CHARS})"),
    re.compile(rf"(?:我叫你|叫你|你叫|你就叫|称呼你|我管你叫)[:： ]*({_NAME_CHARS})"),
]


def _normalize_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip("：:，,。！？!?.~～、 \n\r\t\"'()（）[]【】")
    if not cleaned or cleaned in _INVALID_NAMES:
        return None
    return cleaned


def _extract_name(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    found: str | None = None
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = _normalize_name(match.group(1))
            if candidate:
                found = candidate
    return found


class SoulManager:
    def __init__(self, soul_file: Path) -> None:
        self.soul_file = soul_file

    def exists(self) -> bool:
        return self.soul_file.exists() and self.soul_file.stat().st_size > 0

    def load(self) -> str:
        return self.soul_file.read_text(encoding="utf-8").strip() if self.exists() else ""

    def create(self, name: str, user_name: str, personality: str, voice_id: str, voice_name: str) -> None:
        self.soul_file.parent.mkdir(parents=True, exist_ok=True)
        self.soul_file.write_text(
            _TEMPLATE.format(
                name=name, user_name=user_name, personality=personality,
                voice_id=voice_id, voice_name=voice_name,
            ),
            encoding="utf-8",
        )

    def get_user_name(self) -> str | None:
        for line in self.load().splitlines():
            if "User:" in line:
                return line.split("User:")[-1].strip()
        return None

    def update_voice(self, voice_id: str, voice_name: str) -> None:
        if not self.exists():
            self.create(DEFAULT_ASSISTANT_NAME, DEFAULT_USER_NAME, DEFAULT_PERSONALITY, voice_id, voice_name)
            return
        content = self.load()
        content = re.sub(r"- voice_id: .+", f"- voice_id: {voice_id}", content)
        content = re.sub(r"- voice_name: .+", f"- voice_name: {voice_name}", content)
        self.soul_file.write_text(content, encoding="utf-8")

    def get_voice_id(self) -> str | None:
        for line in self.load().splitlines():
            if "voice_id:" in line:
                return line.split("voice_id:")[-1].strip()
        return None

    def get_name(self) -> str | None:
        for line in self.load().splitlines():
            if "Name:" in line:
                return line.split("Name:")[-1].strip()
        return None

    def needs_identity_bootstrap(self) -> bool:
        return (self.get_user_name() or DEFAULT_USER_NAME) == DEFAULT_USER_NAME or (
            self.get_name() or DEFAULT_ASSISTANT_NAME
        ) == DEFAULT_ASSISTANT_NAME

    def update_identity(
        self,
        *,
        user_name: str | None = None,
        assistant_name: str | None = None,
    ) -> bool:
        current_user_name = self.get_user_name() or DEFAULT_USER_NAME
        current_assistant_name = self.get_name() or DEFAULT_ASSISTANT_NAME
        voice_id = self.get_voice_id() or settings.tts_voice_id
        voice_name = ""
        personality = DEFAULT_PERSONALITY

        if self.exists():
            content = self.load()
            personality_match = re.search(r"## Personality\n(.*?)\n## Voice", content, flags=re.DOTALL)
            voice_name_match = re.search(r"- voice_name:\s*(.*)", content)
            if personality_match:
                personality = personality_match.group(1).strip() or DEFAULT_PERSONALITY
            if voice_name_match:
                voice_name = voice_name_match.group(1).strip()

        next_user_name = _normalize_name(user_name) or current_user_name
        next_assistant_name = _normalize_name(assistant_name) or current_assistant_name

        if next_user_name == current_user_name and next_assistant_name == current_assistant_name and self.exists():
            return False

        self.create(
            next_assistant_name,
            next_user_name,
            personality,
            voice_id,
            voice_name,
        )
        return True

    def sync_identity_from_text(self, text: str) -> bool:
        current_user_name = self.get_user_name() or DEFAULT_USER_NAME
        current_assistant_name = self.get_name() or DEFAULT_ASSISTANT_NAME

        inferred_user_name = None if current_user_name != DEFAULT_USER_NAME else _extract_name(text, _USER_NAME_PATTERNS)
        inferred_assistant_name = None if current_assistant_name != DEFAULT_ASSISTANT_NAME else _extract_name(text, _ASSISTANT_NAME_PATTERNS)

        if not inferred_user_name and not inferred_assistant_name:
            return False

        return self.update_identity(
            user_name=inferred_user_name,
            assistant_name=inferred_assistant_name,
        )


soul_manager = SoulManager(Path(settings.soul_file))
