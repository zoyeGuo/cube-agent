"""Slot extractor — structured argument extraction for action intents."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal

from app.core.intent_router import ActionIntent
from app.core.model_adapter import call_once
from app.tools.builtin.file_tool import inspect_directory

_FILENAME_RULE = r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._()（） -]*\.[A-Za-z0-9]+"

_EXTRACTOR_SYSTEM = """
你是一个动作参数提取器，只输出 JSON。
给定一个动作 intent 和一条用户消息，请提取该动作所需参数。

返回格式：
{"arguments":{},"metadata":{}}

规则：
- 只输出 JSON，不要解释
- 参数不明确时，保留为空字符串、空数组或不返回
- metadata 用于补充展示信息，例如 filename/path/content
""".strip()


@dataclass
class SlotExtraction:
    intent: ActionIntent
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: Literal["rule", "model"] = "rule"


def _clean_quoted_text(text: str) -> str:
    cleaned = text.strip().strip("。！？!?,， ")
    return cleaned.strip("\"'“”‘’")


def _sanitize_filename_candidate(candidate: str) -> str:
    cleaned = _clean_quoted_text(candidate).replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"^(?:请|请你|帮我|麻烦你)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:在)?(?:桌面|desktop)(?:上)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:有)?(?:一个|个)", "", cleaned)
    cleaned = re.sub(r"^(?:创建|生成|写(?:入)?|新建)(?:一个|个)?", "", cleaned)
    cleaned = re.sub(r"^(?:文件|文档)(?:名为|叫做|叫)?", "", cleaned)
    cleaned = re.sub(r"^(?:名为|叫做|叫)", "", cleaned)
    cleaned = re.sub(r"^(?:一个|个)", "", cleaned)
    return cleaned.lstrip(" :：-_")


def _sanitize_delete_filename_candidate(candidate: str) -> str:
    cleaned = _clean_quoted_text(candidate).replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"^(?:请|请你|帮我|麻烦你)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:把)?", "", cleaned)
    cleaned = re.sub(r"^(?:删除|删掉|移除|去掉)", "", cleaned)
    cleaned = re.sub(r"^(?:在)?(?:桌面|desktop)(?:上的|上|里|中的)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:有)?(?:一个|个)", "", cleaned)
    cleaned = re.sub(r"^(?:的)?", "", cleaned)
    cleaned = re.sub(r"(?:文件|文档)$", "", cleaned)
    return cleaned.strip(" :：-_，。")


def _normalize_time_phrase(text: str) -> str:
    cleaned = _clean_quoted_text(text)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.replace("个", "")
    cleaned = re.sub(r"后的?$", "后", cleaned)
    cleaned = re.sub(r"之?后$", "后", cleaned)
    cleaned = re.sub(r"^(?:在|于)", "", cleaned)
    if re.fullmatch(r"[0-9一二两三四五六七八九十百半]+\s*(分钟|小时|天)", cleaned):
        return cleaned + "后"
    return cleaned


def _cleanup_content_candidate(text: str) -> str:
    candidate = text.strip()
    for marker in ("\n", "文件名", "名字", "命名", "不能重名", "不要重名"):
        if marker in candidate:
            candidate = candidate.split(marker, 1)[0].strip(" ，,。！？")
    return _clean_quoted_text(candidate)


def _looks_like_directory_only_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in {"desktop", "~/desktop", "桌面", "~/桌面", "documents", "~/documents", "downloads", "~/downloads"}:
        return True
    basename = normalized.split("/")[-1]
    return basename.lower() in {"desktop", "documents", "downloads", "桌面"}


def _extract_write_content_hint(text: str) -> str:
    content_match = re.search(r"内容(?:是|为)?\s*[\"“”']?(.+?)[\"”']?(?:[。！？]|$)", text, flags=re.DOTALL)
    if content_match:
        return _cleanup_content_candidate(content_match.group(1))
    if "hello world" in text.lower():
        return 'print("hello world")' if ("python" in text.lower() or "脚本" in text) else "hello world"
    return ""


def _allows_auto_filename(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in text or token in lowered
        for token in (
            "你来起", "你来取名", "你来命名", "你来定", "你来写", "你决定",
            "随便起", "自动起", "自动生成", "不能是已经有的文件名", "不能重名", "不要重名",
        )
    )


def _guess_extension_for_write(text: str, content: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in (".py", "python")) or "脚本" in text:
        return "py"
    if any(token in lowered for token in (".md", "markdown")):
        return "md"
    if ".json" in lowered or "json" in lowered:
        return "json"
    if any(token in lowered for token in (".txt",)) or "记事本" in text or "文本" in text:
        return "txt"
    if content.strip().startswith(("print(", "import ", "def ", "class ")):
        return "py"
    return "txt"


def _guess_base_name_for_write(text: str, content: str, extension: str) -> str:
    lowered = text.lower()
    if extension == "py":
        if "hello world" in lowered or "hello world" in content.lower():
            return "hello"
        return "script"
    if extension == "md":
        return "note"
    if extension == "json":
        return "data"
    if "你好世界" in text or "你好世界" in content:
        return "note"
    return "note"


def _unique_desktop_filename(base_name: str, extension: str) -> str:
    listing = inspect_directory("桌面")
    existing = {
        str(entry.get("name", ""))
        for entry in listing.get("entries", [])
    } if listing.get("ok") else set()
    stem = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "_", base_name).strip("_") or "file"
    candidate = f"{stem}.{extension}"
    if candidate not in existing:
        return candidate
    for index in range(1, 1000):
        candidate = f"{stem}_{index}.{extension}"
        if candidate not in existing:
            return candidate
    return f"{stem}_{len(existing) + 1}.{extension}"


def _autofill_write_slots(text: str, content_hint: str = "") -> SlotExtraction | None:
    lowered = text.lower()
    if "桌面" not in text and "desktop" not in lowered:
        return None
    if not _allows_auto_filename(text):
        return None
    content = content_hint or _extract_write_content_hint(text)
    extension = _guess_extension_for_write(text, content)
    base_name = _guess_base_name_for_write(text, content, extension)
    filename = _unique_desktop_filename(base_name, extension)
    path = f"桌面/{filename}"
    return SlotExtraction(
        intent="write_file",
        arguments={"path": path, "content": content, "mode": "overwrite"},
        metadata={"filename": filename, "path": path, "content": content, "auto_named": True},
        source="rule",
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    candidates = [text]
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _extract_write_slots(text: str) -> SlotExtraction | None:
    lowered = text.lower()
    if "桌面" not in text and "desktop" not in lowered:
        return None

    filename = ""
    filename_patterns = (
        r"(?:创建|生成|写(?:入)?|新建)(?:一个|个)?(?:文件|文档)?(?:名为|叫做|叫)?\s*[\"“”']?([^，。！？\"“”']+\.[A-Za-z0-9]+)",
        r"[\"“”']([^\"“”']+\.[A-Za-z0-9]+)[\"“”']",
    )
    for pattern in filename_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _sanitize_filename_candidate(match.group(1))
        if re.fullmatch(_FILENAME_RULE, candidate):
            filename = candidate
            break

    if not filename:
        candidates = re.findall(rf"({_FILENAME_RULE})", text)
        for candidate in reversed(candidates):
            normalized = _sanitize_filename_candidate(candidate)
            if re.fullmatch(_FILENAME_RULE, normalized):
                filename = normalized
                break

    if not filename:
        return None

    content_match = re.search(r"内容(?:是|为)?\s*[\"“”']?(.+?)[\"”']?(?:[。！？]|$)", text, flags=re.DOTALL)
    content = _cleanup_content_candidate(content_match.group(1)) if content_match else ""
    path = f"桌面/{filename}"
    return SlotExtraction(
        intent="write_file",
        arguments={"path": path, "content": content, "mode": "overwrite"},
        metadata={"filename": filename, "path": path, "content": content},
        source="rule",
    )


def _extract_edit_slots(text: str) -> SlotExtraction | None:
    lowered = text.lower()
    if "桌面" not in text and "desktop" not in lowered:
        return None
    if not any(token in text for token in ("修改", "改成", "改为", "替换")):
        return None

    filename = ""
    candidates = re.findall(rf"({_FILENAME_RULE})", text)
    for candidate in reversed(candidates):
        normalized = _sanitize_filename_candidate(candidate)
        if re.fullmatch(_FILENAME_RULE, normalized):
            filename = normalized
            break
    if not filename:
        return None

    new_content = ""
    for pattern in (
        r"(?:内容)?改(?:为|成)\s*[\"“”']?(.+?)[\"”']?(?:[。！？]|$)",
        r"替换(?:为|成)\s*[\"“”']?(.+?)[\"”']?(?:[。！？]|$)",
    ):
        match = re.search(pattern, text)
        if match:
            new_content = _clean_quoted_text(match.group(1))
            break
    if not new_content:
        return None

    path = f"桌面/{filename}"
    return SlotExtraction(
        intent="edit_file",
        arguments={"path": path, "new_content": new_content, "mode": "overwrite"},
        metadata={"filename": filename, "path": path, "new_content": new_content, "mode": "overwrite"},
        source="rule",
    )


def _extract_delete_slots(text: str) -> SlotExtraction | None:
    lowered = text.lower()
    if "桌面" not in text and "desktop" not in lowered:
        return None

    filenames: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"[\"“‘']([^\"”’']+\.[A-Za-z0-9]+)[\"”’']", text):
        candidate = _sanitize_delete_filename_candidate(match)
        if candidate and candidate not in seen:
            filenames.append(candidate)
            seen.add(candidate)

    raw_candidates = re.findall(r"([^\s，。！？、,和及]+?\.[A-Za-z0-9]+)", text)
    for candidate in raw_candidates:
        normalized = _sanitize_delete_filename_candidate(candidate)
        if re.fullmatch(_FILENAME_RULE, normalized) and normalized not in seen:
            filenames.append(normalized)
            seen.add(normalized)

    if not filenames:
        return None

    return SlotExtraction(
        intent="delete_file",
        arguments={"filenames": filenames},
        metadata={"filenames": filenames},
        source="rule",
    )


def _extract_reminder_slots(text: str) -> SlotExtraction | None:
    message_match = re.search(r"提醒我(.+?)(?:[。！？]|$)", text)
    message = _clean_quoted_text(message_match.group(1)) if message_match else ""
    if not message:
        return None

    time_str = ""
    for pattern in (
        r"设置(?:一个|个)?(.+?)提醒",
        r"(.+?)后提醒我",
        r"(.+?)提醒我",
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = _normalize_time_phrase(match.group(1))
        if any(token in candidate for token in ("分钟", "小时", "天后", "今天", "明天", "后天", "今晚", "早上", "下午", "晚上", "点")):
            time_str = candidate
            break

    if not time_str:
        return None

    return SlotExtraction(
        intent="set_reminder",
        arguments={"time_str": time_str, "message": message},
        metadata={"time_str": time_str, "message": message},
        source="rule",
    )


def _extract_open_slots(text: str) -> SlotExtraction | None:
    match = re.search(r"(?:打开|启动|运行)\s*(.+?)(?:[。！？]|$)", text)
    if not match:
        return None
    target = _clean_quoted_text(match.group(1))
    target = re.sub(r"(?:文件|文件夹|应用程序|应用)$", "", target).strip()
    if not target or target in {"一下", "看看"}:
        return None

    if "桌面" in target:
        raw_candidates = re.findall(r"([^\s，。！？、,和及]+?\.[A-Za-z0-9]+)", target)
        for candidate in reversed(raw_candidates):
            normalized = _sanitize_filename_candidate(candidate)
            if re.fullmatch(_FILENAME_RULE, normalized):
                target = f"桌面/{normalized}"
                break

    return SlotExtraction(
        intent="open_file",
        arguments={"path": target},
        metadata={"path": target},
        source="rule",
    )


def _extract_run_command_slots(text: str) -> SlotExtraction | None:
    stripped = _clean_quoted_text(text)
    command = ""
    cwd = ""

    fenced = re.search(r"`([^`]+)`", text)
    if fenced:
        command = _clean_quoted_text(fenced.group(1))

    if not command:
        scoped = re.search(r"在\s*([^\s，。！？]+?)\s*(?:目录|文件夹)?(?:下)?(?:执行|运行)(?:命令)?\s+(.+?)(?:[。！？]|$)", text)
        if scoped:
            cwd = _clean_quoted_text(scoped.group(1))
            command = _clean_quoted_text(scoped.group(2))

    if not command:
        direct = re.search(r"(?:执行|运行)(?:命令|终端命令|shell 命令|CLI 命令)?\s+(.+?)(?:[。！？]|$)", text, flags=re.IGNORECASE)
        if direct:
            command = _clean_quoted_text(direct.group(1))

    if not command:
        command_starters = (
            "ls", "pwd", "cd", "git", "npm", "pnpm", "yarn", "node", "python", "python3",
            "uvicorn", "pip", "rg", "cat", "mkdir", "touch", "echo", "cp", "mv", "find",
            "ps", "lsof", "curl", "bash", "sh", "zsh", "osascript",
        )
        lowered = stripped.lower()
        if any(lowered == starter or lowered.startswith(starter + " ") for starter in command_starters):
            command = stripped

    if not command:
        return None

    command = command.strip("\"'“”‘’ ")
    if not command:
        return None

    return SlotExtraction(
        intent="run_command",
        arguments={"command": command, "cwd": cwd},
        metadata={"command": command, "cwd": cwd},
        source="rule",
    )


def _rule_extract(text: str, intent: ActionIntent) -> SlotExtraction | None:
    if intent == "write_file":
        return _extract_write_slots(text)
    if intent == "edit_file":
        return _extract_edit_slots(text)
    if intent == "delete_file":
        return _extract_delete_slots(text)
    if intent == "set_reminder":
        return _extract_reminder_slots(text)
    if intent == "open_file":
        return _extract_open_slots(text)
    if intent == "run_command":
        return _extract_run_command_slots(text)
    return None


async def _extract_with_model(text: str, intent: ActionIntent) -> SlotExtraction | None:
    raw = await call_once(
        messages=[{"role": "user", "content": f"intent={intent}\nuser_message={text}"}],
        system=_EXTRACTOR_SYSTEM,
        max_tokens=260,
    )
    data = _extract_json_object(raw)
    if not data:
        return None
    arguments = data.get("arguments", {})
    metadata = data.get("metadata", {})
    if not isinstance(arguments, dict):
        arguments = {}
    if not isinstance(metadata, dict):
        metadata = {}

    if intent == "write_file":
        path = str(arguments.get("path", "")).strip()
        content = str(arguments.get("content", ""))
        if not path or _looks_like_directory_only_path(path):
            return _autofill_write_slots(text, content)
        filename = path.replace("\\", "/").split("/")[-1]
        return SlotExtraction(
            intent=intent,
            arguments={"path": path, "content": content, "mode": str(arguments.get("mode", "overwrite")) or "overwrite"},
            metadata={
                "filename": metadata.get("filename", filename),
                "path": path,
                "content": metadata.get("content", content),
            },
            source="model",
        )

    if intent == "edit_file":
        path = str(arguments.get("path", "")).strip()
        new_content = str(arguments.get("new_content", "")).strip()
        mode = str(arguments.get("mode", "overwrite")).strip() or "overwrite"
        if not path or _looks_like_directory_only_path(path) or not new_content:
            return None
        filename = path.replace("\\", "/").split("/")[-1]
        return SlotExtraction(
            intent=intent,
            arguments={"path": path, "new_content": new_content, "mode": mode},
            metadata={
                "filename": metadata.get("filename", filename),
                "path": path,
                "new_content": metadata.get("new_content", new_content),
                "mode": metadata.get("mode", mode),
            },
            source="model",
        )

    if intent == "delete_file":
        filenames = arguments.get("filenames", [])
        if not isinstance(filenames, list):
            return None
        normalized = [str(item).strip() for item in filenames if str(item).strip()]
        if not normalized:
            return None
        return SlotExtraction(
            intent=intent,
            arguments={"filenames": normalized},
            metadata={"filenames": metadata.get("filenames", normalized)},
            source="model",
        )

    if intent == "set_reminder":
        time_str = str(arguments.get("time_str", "")).strip()
        message = str(arguments.get("message", "")).strip()
        if not time_str or not message:
            return None
        return SlotExtraction(
            intent=intent,
            arguments={"time_str": time_str, "message": message},
            metadata={"time_str": time_str, "message": message},
            source="model",
        )

    if intent == "open_file":
        path = str(arguments.get("path", "")).strip()
        if not path or _looks_like_directory_only_path(path):
            return None
        return SlotExtraction(
            intent=intent,
            arguments={"path": path},
            metadata={"path": path},
            source="model",
        )

    if intent == "run_command":
        command = str(arguments.get("command", "")).strip()
        cwd = str(arguments.get("cwd", "")).strip()
        if not command:
            return None
        return SlotExtraction(
            intent=intent,
            arguments={"command": command, "cwd": cwd},
            metadata={"command": metadata.get("command", command), "cwd": metadata.get("cwd", cwd)},
            source="model",
        )

    return None


async def extract_action_slots(text: str, intent: ActionIntent) -> SlotExtraction | None:
    slots = _rule_extract(text, intent)
    if slots:
        return slots
    try:
        slots = await _extract_with_model(text, intent)
    except Exception:
        slots = None
    if slots:
        return slots
    if intent == "write_file":
        return _autofill_write_slots(text)
    return None
