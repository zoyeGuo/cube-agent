"""Execution result verification for side-effect actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VerificationResult:
    ok: bool
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _resolve_user_path(*, path: str | None = None, filename: str | None = None) -> Path:
    if path:
        text = str(path).strip().replace("\\", "/")
        home = Path.home()
        desktop = home / "Desktop"

        if text in {"Desktop", "desktop", "桌面", "~/Desktop", "~/桌面"}:
            return desktop.resolve()
        if text.startswith(("Desktop/", "desktop/", "桌面/")):
            return (desktop / text.split("/", 1)[1]).resolve()
        for marker in ("/Desktop/", "/桌面/"):
            if marker in text:
                return (desktop / text.split(marker, 1)[1]).resolve()
        return Path(text).expanduser().resolve()

    if filename:
        return (Path.home() / "Desktop" / filename).resolve()

    raise ValueError("path or filename is required")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def verify_write_file(
    *,
    output: str,
    filename: str | None = None,
    path: str | None = None,
    expected_content: str | None = None,
) -> VerificationResult:
    if not output.startswith(("已写入：", "已追加写入：")):
        return VerificationResult(ok=False, message=output)
    file_path = _resolve_user_path(path=path, filename=filename)
    if not (file_path.exists() and file_path.is_file()):
        label = filename or file_path.name
        return VerificationResult(ok=False, message=f"写入失败：{label} 没有出现在预期位置")
    details: dict[str, Any] = {"path": str(file_path)}
    if expected_content is not None:
        actual = _read_text(file_path)
        details["actual_content"] = actual
        if actual != expected_content:
            return VerificationResult(
                ok=False,
                message=f"写入失败：{file_path.name} 的内容与预期不一致",
                details=details,
            )
    return VerificationResult(ok=True, details=details)


def verify_edit_file(*, path: str, expected_content: str, output: str) -> VerificationResult:
    if not output.startswith("已修改："):
        return VerificationResult(ok=False, message=output)
    file_path = _resolve_user_path(path=path)
    if not (file_path.exists() and file_path.is_file()):
        return VerificationResult(ok=False, message=f"修改失败：{file_path.name} 不存在", details={"path": str(file_path)})
    actual = _read_text(file_path)
    details = {"path": str(file_path), "actual_content": actual}
    if actual != expected_content:
        return VerificationResult(
            ok=False,
            message=f"修改失败：{file_path.name} 的内容没有变成预期值",
            details=details,
        )
    return VerificationResult(ok=True, details=details)


def verify_delete_file(*, output: str, filename: str | None = None, path: str | None = None) -> VerificationResult:
    if not output.startswith("已删除："):
        return VerificationResult(ok=False, message=output)
    file_path = _resolve_user_path(path=path, filename=filename)
    if not file_path.exists():
        return VerificationResult(ok=True, details={"path": str(file_path)})
    return VerificationResult(ok=False, message=f"删除失败：{file_path.name} 仍然存在", details={"path": str(file_path)})


def _count_matching_reminders(items: list[dict[str, Any]], message: str) -> int:
    return sum(1 for item in items if item.get("message") == message)


def verify_set_reminder(
    *,
    output: str,
    before_items: list[dict[str, Any]],
    after_items: list[dict[str, Any]],
    message: str,
    failure_prefixes: tuple[str, ...],
) -> VerificationResult:
    if any(output.startswith(prefix) for prefix in failure_prefixes):
        return VerificationResult(ok=False, message=output)
    before_count = _count_matching_reminders(before_items, message)
    after_count = _count_matching_reminders(after_items, message)
    details = {"before_count": before_count, "after_count": after_count, "message": message}
    if after_count > before_count:
        return VerificationResult(ok=True, details=details)
    return VerificationResult(ok=False, message="提醒看起来没有真正创建成功，请重试。", details=details)


def verify_open_file(*, output: str) -> VerificationResult:
    if output.startswith("已打开："):
        return VerificationResult(ok=True, details={"output": output})
    return VerificationResult(ok=False, message=output, details={"output": output})


def verify_execute_python(*, output: str) -> VerificationResult:
    failure_prefixes = ("沙箱拒绝：", "执行超时", "执行错误：", "[stderr]")
    if any(output.startswith(prefix) for prefix in failure_prefixes):
        return VerificationResult(ok=False, message=output, details={"output": output})
    return VerificationResult(ok=True, details={"output": output})


def verify_run_command(*, output: str) -> VerificationResult:
    failure_prefixes = (
        "安全策略拒绝：",
        "权限错误：",
        "执行超时",
        "命令不存在",
        "命令执行错误：",
        "命令执行失败（",
    )
    if any(output.startswith(prefix) for prefix in failure_prefixes):
        return VerificationResult(ok=False, message=output, details={"output": output})
    if output.startswith("命令已执行（"):
        return VerificationResult(ok=True, details={"output": output})
    return VerificationResult(ok=False, message=output, details={"output": output})
