"""Action engine — capability registry + receipt-based execution for side-effect actions."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import uuid

from app.core.action_verifier import (
    VerificationResult,
    verify_delete_file,
    verify_edit_file,
    verify_execute_python,
    verify_open_file,
    verify_run_command,
    verify_set_reminder,
    verify_write_file,
)
from app.core.intent_router import ActionIntent, IntentRoute, classify_action_intent
from app.core.model_adapter import ToolCall
from app.core.slot_extractor import SlotExtraction, extract_action_slots
from app.models.pending_action import PendingAction, PendingActionStep
from app.schemas.events import ChoiceEvent, ChoiceItem, ReminderItem, ScheduleEvent, StateEvent
from app.services.scheduler import list_reminders
from app.store.pending_action_store import pending_action_store
from app.store.pending_slot_store import pending_slot_store
from app.store.session_store import session_store
from app.tools.executor import run_tool_call
from app.core.debug_trace import MarkdownDebugTrace

_FAILURE_PREFIXES = (
    "错误：", "失败：", "工具执行错误：", "权限错误：",
    "沙箱拒绝：", "执行超时", "不存在：", "不是文件：", "不是目录：",
)
_BLOCKED_PREFIXES = ("权限错误：", "沙箱拒绝：", "执行命令受限：", "错误：不允许", "安全策略拒绝：")
_TRACKED_TOOL_ACTIONS = {
    "write_file", "edit_file", "delete_file", "open_file",
    "set_reminder", "run_command", "execute_python",
}


@dataclass
class ExecutionReceipt:
    capability_id: str
    tool_calls: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    expected_effect: dict[str, Any] = field(default_factory=dict)
    observed_effect: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    failure_reason: str | None = None
    attempts: int = 0


@dataclass
class ActionHandlingResult:
    handled: bool = False
    assistant_text: str = ""
    tools_called: set[str] = field(default_factory=set)
    receipt: ExecutionReceipt | None = None


@dataclass
class ActionCapability:
    intent: ActionIntent
    requires_confirmation: bool
    retry_on_verify_failure: bool
    build_action: Callable[[str, SlotExtraction], PendingAction]
    verify: Callable[[str, PendingAction, list[str]], ExecutionReceipt | Awaitable[ExecutionReceipt]]
    compose_success: Callable[[PendingAction, ExecutionReceipt], str]
    after_success: Callable[[str, PendingAction, ExecutionReceipt, Callable[[Any], None]], None | Awaitable[None]] | None = None


def _new_pending_action(intent: str, title: str, steps: list[PendingActionStep], metadata: dict | None = None) -> PendingAction:
    return PendingAction(
        action_id=f"act_{uuid.uuid4().hex[:12]}",
        intent=intent,
        title=title,
        steps=steps,
        metadata=metadata or {},
    )


def _describe_path(path: str) -> str:
    text = str(path).replace("\\", "/")
    if text.startswith(("桌面/", "Desktop/", "desktop/")):
        return text
    return Path(text).name or text


def tracks_tool_call_action(tc_or_name: ToolCall | str) -> bool:
    name = tc_or_name if isinstance(tc_or_name, str) else tc_or_name.name
    return name in _TRACKED_TOOL_ACTIONS


def pending_action_from_tool_call(
    tc: ToolCall,
    title: str,
    *,
    session_id: str | None = None,
) -> PendingAction:
    metadata: dict[str, Any] = {}
    if tc.name == "open_file":
        metadata["path"] = str(tc.arguments.get("path", ""))
    elif tc.name == "set_reminder":
        metadata["time_str"] = str(tc.arguments.get("time_str", ""))
        metadata["message"] = str(tc.arguments.get("message", ""))
        metadata["before_items"] = list_reminders(session_id) if session_id else []
    elif tc.name == "execute_python":
        metadata["code"] = str(tc.arguments.get("code", ""))
    elif tc.name == "write_file":
        path = str(tc.arguments.get("path", ""))
        metadata["path"] = path
        metadata["content"] = str(tc.arguments.get("content", ""))
        metadata["filename"] = path.replace("\\", "/").split("/")[-1]
    elif tc.name == "edit_file":
        path = str(tc.arguments.get("path", ""))
        metadata["path"] = path
        metadata["new_content"] = str(tc.arguments.get("new_content", ""))
        metadata["filename"] = path.replace("\\", "/").split("/")[-1]
    elif tc.name == "delete_file":
        path = str(tc.arguments.get("path", ""))
        metadata["path"] = path
        metadata["filename"] = path.replace("\\", "/").split("/")[-1]
        metadata["filenames"] = [metadata["filename"]] if metadata["filename"] else []
    elif tc.name == "run_command":
        metadata["command"] = str(tc.arguments.get("command", ""))
        metadata["cwd"] = str(tc.arguments.get("cwd", ""))
    return _new_pending_action(
        intent=tc.name,
        title=title,
        steps=[PendingActionStep(tool_name=tc.name, arguments=dict(tc.arguments))],
        metadata=metadata,
    )


def confirmation_choice_items(intent: str) -> list[ChoiceItem]:
    if intent == "delete_file":
        return [
            ChoiceItem(id="confirm", label="确认删除", tag="继续删除这一步", recommended=True),
            ChoiceItem(id="cancel", label="取消", tag="本次不删除"),
        ]
    if intent == "open_file":
        return [
            ChoiceItem(id="confirm", label="确认打开", tag="继续打开这个目标", recommended=True),
            ChoiceItem(id="cancel", label="取消", tag="本次不打开"),
        ]
    if intent == "execute_python":
        return [
            ChoiceItem(id="confirm", label="确认执行", tag="继续执行这段代码", recommended=True),
            ChoiceItem(id="cancel", label="取消", tag="本次不执行"),
        ]
    if intent == "run_command":
        return [
            ChoiceItem(id="confirm", label="确认执行", tag="继续执行这条命令", recommended=True),
            ChoiceItem(id="cancel", label="取消", tag="本次不执行"),
        ]
    if intent == "edit_file":
        return [
            ChoiceItem(id="confirm", label="确认修改", tag="继续修改这个文件", recommended=True),
            ChoiceItem(id="cancel", label="取消", tag="本次不修改"),
        ]
    return [
        ChoiceItem(id="confirm", label="确认执行", tag="继续执行这一步", recommended=True),
        ChoiceItem(id="cancel", label="取消", tag="本次不执行"),
    ]


async def execute_pending_action_steps(action: PendingAction) -> list[str]:
    outputs: list[str] = []
    for index, step in enumerate(action.steps):
        output = await run_tool_call(
            ToolCall(
                id=f"{action.action_id}_{index}",
                name=step.tool_name,
                arguments=step.arguments,
            ),
            force_confirmed=True,
            bypass_confirmation=True,
        )
        outputs.append(output)
    return outputs


def _base_receipt(capability_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    return ExecutionReceipt(
        capability_id=capability_id,
        tool_calls=[step.tool_name for step in action.steps],
        outputs=list(outputs),
        attempts=1,
    )


def _apply_verification(
    *,
    capability_id: str,
    action: PendingAction,
    outputs: list[str],
    verification: VerificationResult,
    expected_effect: dict[str, Any],
) -> ExecutionReceipt:
    return ExecutionReceipt(
        capability_id=capability_id,
        tool_calls=[step.tool_name for step in action.steps],
        outputs=list(outputs),
        expected_effect=expected_effect,
        observed_effect=dict(verification.details),
        verified=verification.ok,
        failure_reason=None if verification.ok else verification.message,
        attempts=1,
    )


def finalize_pending_action_outputs(action: PendingAction, outputs: list[str]) -> list[str]:
    if not outputs:
        return ["这一步没有产出结果。"]

    normalized = list(outputs)
    if action.intent == "open_file":
        verification = verify_open_file(output=normalized[0])
        if not verification.ok and verification.message:
            normalized[0] = verification.message
    elif action.intent == "execute_python":
        verification = verify_execute_python(output=normalized[0])
        if not verification.ok and verification.message:
            normalized[0] = verification.message
    elif action.intent == "write_file":
        verification = verify_write_file(
            path=str(action.metadata.get("path", "")),
            filename=str(action.metadata.get("filename", "")) or None,
            expected_content=str(action.metadata.get("content", "")),
            output=normalized[0],
        )
        if not verification.ok and verification.message:
            normalized[0] = verification.message
    elif action.intent == "edit_file":
        verification = verify_edit_file(
            path=str(action.metadata.get("path", "")),
            expected_content=str(action.metadata.get("new_content", "")),
            output=normalized[0],
        )
        if not verification.ok and verification.message:
            normalized[0] = verification.message
    elif action.intent == "delete_file":
        verification = verify_delete_file(
            path=str(action.metadata.get("path", "")),
            filename=str(action.metadata.get("filename", "")) or None,
            output=normalized[0],
        )
        if not verification.ok and verification.message:
            normalized[0] = verification.message
    elif action.intent == "run_command":
        verification = verify_run_command(output=normalized[0])
        if not verification.ok and verification.message:
            normalized[0] = verification.message
    return normalized


def _friendly_write_result(path: str, content: str, output: str) -> str:
    filename = Path(path.replace("\\", "/")).name
    if output.startswith(("已写入：", "已追加写入：")):
        if content:
            return f"已把 {filename} 写成“{content}”。"
        return f"已写入 {filename}。"
    return output


def _friendly_edit_result(path: str, new_content: str, output: str) -> str:
    filename = Path(path.replace("\\", "/")).name
    if output.startswith("已修改："):
        return f"已把 {filename} 的内容改为“{new_content}”。"
    return output


def _friendly_delete_result(filenames: list[str], outputs: list[str]) -> str:
    deleted: list[str] = []
    missing: list[str] = []
    failures: list[str] = []
    for filename, output in zip(filenames, outputs):
        if output.startswith("已删除："):
            deleted.append(filename)
        elif output.startswith("文件不存在："):
            missing.append(filename)
        else:
            failures.append(output)

    if deleted and not missing and not failures:
        return f"已从桌面删除 {'、'.join(deleted)}。"
    if deleted and (missing or failures):
        parts = [f"已删除 {'、'.join(deleted)}"]
        if missing:
            parts.append(f"没找到 {'、'.join(missing)}")
        if failures:
            parts.append(f"其余失败：{'；'.join(failures)}")
        return "，".join(parts) + "。"
    if missing and not failures:
        return f"桌面上没有找到 {'、'.join(missing)}。"
    return "；".join(failures) if failures else "删除没有执行。"


async def _maybe_await(value: Any) -> Any:
    if isinstance(value, Awaitable):
        return await value
    return value


async def _confirm_pending_action(
    *,
    session_id: str,
    action: PendingAction,
    emit: Callable[[Any], None],
    ensure_not_cancelled: Callable[[], None],
) -> tuple[PendingAction | None, str | None]:
    pending_action_store.create(session_id, action)
    emit(ChoiceEvent(
        choice_id="confirm_execution",
        title=action.title,
        items=confirmation_choice_items(action.intent),
    ))
    emit(StateEvent(name="waiting_user", scope="execution"))
    resumed_action, answer = await pending_action_store.wait(session_id, timeout=300)
    ensure_not_cancelled()
    return resumed_action or action, answer


def _build_write_action(_session_id: str, slots: SlotExtraction) -> PendingAction:
    path = str(slots.arguments.get("path", ""))
    content = str(slots.arguments.get("content", ""))
    filename = str(slots.metadata.get("filename", Path(path).name))
    title = f"请确认：在 {_describe_path(path)} 创建或覆盖 {filename}"
    if content:
        title += f"，内容 {content}"
    title += "。"
    return _new_pending_action(
        intent="write_file",
        title=title,
        steps=[PendingActionStep(tool_name="write_file", arguments=dict(slots.arguments))],
        metadata={"filename": filename, "path": path, "content": content},
    )


def _build_edit_action(_session_id: str, slots: SlotExtraction) -> PendingAction:
    path = str(slots.arguments.get("path", ""))
    new_content = str(slots.arguments.get("new_content", ""))
    filename = str(slots.metadata.get("filename", Path(path).name))
    title = f"请确认：把 {_describe_path(path)} 的内容改为 {new_content}。"
    return _new_pending_action(
        intent="edit_file",
        title=title,
        steps=[PendingActionStep(tool_name="edit_file", arguments=dict(slots.arguments))],
        metadata={"filename": filename, "path": path, "new_content": new_content},
    )


def _build_delete_action(_session_id: str, slots: SlotExtraction) -> PendingAction:
    filenames = list(slots.metadata.get("filenames", []))
    return _new_pending_action(
        intent="delete_file",
        title=f"请确认：删除桌面上的 {'、'.join(filenames)}。",
        steps=[PendingActionStep(tool_name="delete_file", arguments={"path": f"桌面/{filename}"}) for filename in filenames],
        metadata={"filenames": filenames},
    )


def _build_open_action(_session_id: str, slots: SlotExtraction) -> PendingAction:
    path = str(slots.metadata.get("path", slots.arguments.get("path", "")))
    return _new_pending_action(
        intent="open_file",
        title=f"请确认：打开 {path}。",
        steps=[PendingActionStep(tool_name="open_file", arguments={"path": path})],
        metadata={"path": path},
    )


def _build_reminder_action(session_id: str, slots: SlotExtraction) -> PendingAction:
    before_items = list_reminders(session_id)
    return _new_pending_action(
        intent="set_reminder",
        title=f"设置提醒：{slots.metadata['time_str']} - {slots.metadata['message']}",
        steps=[PendingActionStep(tool_name="set_reminder", arguments=dict(slots.arguments))],
        metadata={**dict(slots.metadata), "before_items": before_items},
    )


def _build_command_action(_session_id: str, slots: SlotExtraction) -> PendingAction:
    command = str(slots.metadata.get("command", slots.arguments.get("command", ""))).strip()
    cwd = str(slots.metadata.get("cwd", slots.arguments.get("cwd", ""))).strip()
    title = f"请确认：执行命令 {command}"
    if cwd:
        title += f"（目录：{cwd}）"
    title += "。"
    return _new_pending_action(
        intent="run_command",
        title=title,
        steps=[PendingActionStep(tool_name="run_command", arguments=dict(slots.arguments))],
        metadata={"command": command, "cwd": cwd},
    )


def _verify_write(_session_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    normalized = finalize_pending_action_outputs(action, outputs)
    verification = verify_write_file(
        path=str(action.metadata.get("path", "")),
        filename=str(action.metadata.get("filename", "")) or None,
        expected_content=str(action.metadata.get("content", "")),
        output=normalized[0],
    )
    receipt = _apply_verification(
        capability_id="write_file",
        action=action,
        outputs=normalized,
        verification=verification,
        expected_effect={
            "path": str(action.metadata.get("path", "")),
            "content": str(action.metadata.get("content", "")),
        },
    )
    return receipt


def _verify_edit(_session_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    normalized = finalize_pending_action_outputs(action, outputs)
    verification = verify_edit_file(
        path=str(action.metadata.get("path", "")),
        expected_content=str(action.metadata.get("new_content", "")),
        output=normalized[0],
    )
    receipt = _apply_verification(
        capability_id="edit_file",
        action=action,
        outputs=normalized,
        verification=verification,
        expected_effect={
            "path": str(action.metadata.get("path", "")),
            "new_content": str(action.metadata.get("new_content", "")),
        },
    )
    return receipt


def _verify_delete(_session_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    filenames = list(action.metadata.get("filenames", []))
    normalized_outputs: list[str] = []
    deleted: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for filename, output in zip(filenames, outputs):
        verification = verify_delete_file(path=f"桌面/{filename}", filename=filename, output=output)
        normalized = verification.message if not verification.ok and verification.message else output
        normalized_outputs.append(normalized)
        if normalized.startswith("已删除："):
            deleted.append(filename)
        elif normalized.startswith("文件不存在："):
            missing.append(filename)
        else:
            failed.append(normalized)
    return ExecutionReceipt(
        capability_id="delete_file",
        tool_calls=[step.tool_name for step in action.steps],
        outputs=normalized_outputs,
        expected_effect={"filenames": filenames},
        observed_effect={"deleted": deleted, "missing": missing, "failed": failed},
        verified=not failed,
        failure_reason="；".join(failed) if failed else None,
        attempts=1,
    )


def _verify_open(_session_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    normalized = finalize_pending_action_outputs(action, outputs)
    verification = verify_open_file(output=normalized[0])
    return _apply_verification(
        capability_id="open_file",
        action=action,
        outputs=normalized,
        verification=verification,
        expected_effect={"path": str(action.metadata.get("path", ""))},
    )


def _verify_reminder(session_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    assistant_text = outputs[0] if outputs else "提醒没有产出结果。"
    after_items = list_reminders(session_id)
    verification = verify_set_reminder(
        output=assistant_text,
        before_items=list(action.metadata.get("before_items", [])),
        after_items=after_items,
        message=str(action.metadata.get("message", "")),
        failure_prefixes=_FAILURE_PREFIXES,
    )
    normalized = [verification.message if not verification.ok and verification.message else assistant_text]
    receipt = _apply_verification(
        capability_id="set_reminder",
        action=action,
        outputs=normalized,
        verification=verification,
        expected_effect={
            "time_str": str(action.metadata.get("time_str", "")),
            "message": str(action.metadata.get("message", "")),
        },
    )
    receipt.observed_effect["reminders"] = after_items
    return receipt


def _verify_command(_session_id: str, action: PendingAction, outputs: list[str]) -> ExecutionReceipt:
    normalized = finalize_pending_action_outputs(action, outputs)
    verification = verify_run_command(output=normalized[0])
    return _apply_verification(
        capability_id="run_command",
        action=action,
        outputs=normalized,
        verification=verification,
        expected_effect={
            "command": str(action.metadata.get("command", "")),
            "cwd": str(action.metadata.get("cwd", "")),
        },
    )


def _compose_write(action: PendingAction, receipt: ExecutionReceipt) -> str:
    return _friendly_write_result(
        str(action.metadata.get("path", "")),
        str(action.metadata.get("content", "")),
        receipt.outputs[0],
    )


def _compose_edit(action: PendingAction, receipt: ExecutionReceipt) -> str:
    return _friendly_edit_result(
        str(action.metadata.get("path", "")),
        str(action.metadata.get("new_content", "")),
        receipt.outputs[0],
    )


def _compose_delete(action: PendingAction, receipt: ExecutionReceipt) -> str:
    return _friendly_delete_result(list(action.metadata.get("filenames", [])), receipt.outputs)


def _compose_first_output(_action: PendingAction, receipt: ExecutionReceipt) -> str:
    return receipt.outputs[0] if receipt.outputs else "这一步没有产出结果。"


async def _emit_schedule_after_success(
    _session_id: str,
    _action: PendingAction,
    receipt: ExecutionReceipt,
    emit: Callable[[Any], None],
) -> None:
    reminders = receipt.observed_effect.get("reminders", [])
    emit(ScheduleEvent(reminders=[ReminderItem(**r) for r in reminders]))


def _action_expected_effect(action: PendingAction) -> dict[str, Any]:
    metadata = dict(action.metadata)
    if action.intent == "delete_file":
        return {"filenames": list(metadata.get("filenames", []))}
    if action.intent == "run_command":
        return {
            "command": str(metadata.get("command", "")),
            "cwd": str(metadata.get("cwd", "")),
        }
    if action.intent == "set_reminder":
        return {
            "time_str": str(metadata.get("time_str", "")),
            "message": str(metadata.get("message", "")),
        }
    if action.intent in {"write_file", "edit_file", "open_file"}:
        keys = ("path", "filename", "content", "new_content")
        return {
            key: metadata[key]
            for key in keys
            if key in metadata and metadata[key]
        }
    return metadata


def _action_label(action: PendingAction) -> str:
    return str(
        action.metadata.get("filename")
        or action.metadata.get("path")
        or action.metadata.get("command")
        or action.intent
    )


def _event_status_for_failure(failure_reason: str) -> str:
    if any(failure_reason.startswith(prefix) for prefix in _BLOCKED_PREFIXES):
        return "blocked"
    return "verified_failure"


def _record_action_event(
    *,
    session_id: str,
    user_request: str,
    action: PendingAction,
    status: str,
    summary: str,
    expected_effect: dict[str, Any] | None = None,
    observed_effect: dict[str, Any] | None = None,
) -> None:
    session_store.persist_action_event(
        session_id=session_id,
        action_id=action.action_id,
        intent=action.intent,
        user_request=user_request,
        status=status,
        summary=summary,
        expected_effect=expected_effect or _action_expected_effect(action),
        observed_effect=observed_effect or {},
    )


def _trace(debug_trace: MarkdownDebugTrace | None, title: str, *, summary: str = "", data: Any | None = None) -> None:
    if debug_trace is not None:
        debug_trace.record(title, summary=summary, data=data)


def _clarify_missing_slots(intent: ActionIntent, user_message: str) -> str:
    if intent == "write_file":
        if "python" in user_message.lower() or ".py" in user_message.lower():
            return "你想把这个 Python 文件命名成什么？比如 `hello.py`。"
        return "你想把这个文件命名成什么？最好给我一个具体文件名，比如 `test.txt`。"
    if intent in {"edit_file", "delete_file", "open_file"}:
        return "你想操作哪个文件？请给我具体文件名或路径。"
    if intent == "set_reminder":
        return "你想在什么时候提醒什么？请把时间和提醒内容说完整。"
    if intent == "run_command":
        return "你想执行什么命令？如果有目录要求，也请一起告诉我。"
    return "这一步还缺少关键信息，请再说具体一点。"


def persist_interrupted_tool_action(
    *,
    session_id: str,
    user_request: str,
    action: PendingAction,
    answer: str | None,
) -> str:
    if answer == "cancel":
        label = _action_label(action)
        _record_action_event(
            session_id=session_id,
            user_request=user_request,
            action=action,
            status="cancelled",
            summary=f"用户取消执行：{label}。",
            observed_effect={"answer": "cancel"},
        )
        return "用户取消了这一步操作。"

    label = _action_label(action)
    _record_action_event(
        session_id=session_id,
        user_request=user_request,
        action=action,
        status="blocked",
        summary=f"用户没有明确确认，这一步操作未执行：{label}。",
        observed_effect={"answer": answer or ""},
    )
    return "用户没有明确确认，这一步操作未执行。"


async def finalize_tracked_tool_action(
    *,
    session_id: str,
    user_request: str,
    action: PendingAction,
    outputs: list[str],
    emit: Callable[[Any], None],
) -> tuple[str, ExecutionReceipt | None]:
    if action.intent in CAPABILITIES:
        capability = CAPABILITIES[action.intent]  # type: ignore[index]
        receipt = await _maybe_await(capability.verify(session_id, action, outputs))
        receipt.attempts = max(receipt.attempts, 1)
        if not receipt.verified:
            failure = receipt.failure_reason or "执行后验证没有通过。"
            _record_action_event(
                session_id=session_id,
                user_request=user_request,
                action=action,
                status=_event_status_for_failure(failure),
                summary=f"我尝试执行了，但验证没通过：{failure}",
                expected_effect=receipt.expected_effect,
                observed_effect={**receipt.observed_effect, "attempts": receipt.attempts},
            )
            return f"失败：执行后验证没通过：{failure}", receipt

        if capability.after_success:
            await _maybe_await(capability.after_success(session_id, action, receipt, emit))
        success_text = capability.compose_success(action, receipt)
        _record_action_event(
            session_id=session_id,
            user_request=user_request,
            action=action,
            status="verified_success",
            summary=success_text,
            expected_effect=receipt.expected_effect,
            observed_effect={**receipt.observed_effect, "attempts": receipt.attempts},
        )
        return success_text, receipt

    if action.intent == "execute_python":
        normalized = finalize_pending_action_outputs(action, outputs)
        verification = verify_execute_python(output=normalized[0])
        receipt = _apply_verification(
            capability_id="execute_python",
            action=action,
            outputs=normalized,
            verification=verification,
            expected_effect={"code": str(action.metadata.get("code", ""))},
        )
        receipt.attempts = 1
        if not receipt.verified:
            failure = receipt.failure_reason or normalized[0]
            _record_action_event(
                session_id=session_id,
                user_request=user_request,
                action=action,
                status=_event_status_for_failure(failure),
                summary=f"我尝试执行了，但验证没通过：{failure}",
                expected_effect=receipt.expected_effect,
                observed_effect={**receipt.observed_effect, "attempts": receipt.attempts},
            )
            return f"失败：执行后验证没通过：{failure}", receipt

        success_text = normalized[0]
        _record_action_event(
            session_id=session_id,
            user_request=user_request,
            action=action,
            status="verified_success",
            summary=success_text,
            expected_effect=receipt.expected_effect,
            observed_effect={**receipt.observed_effect, "attempts": receipt.attempts},
        )
        return success_text, receipt

    return outputs[0] if outputs else "这一步没有产出结果。", None


CAPABILITIES: dict[ActionIntent, ActionCapability] = {
    "write_file": ActionCapability(
        intent="write_file",
        requires_confirmation=True,
        retry_on_verify_failure=True,
        build_action=_build_write_action,
        verify=_verify_write,
        compose_success=_compose_write,
    ),
    "edit_file": ActionCapability(
        intent="edit_file",
        requires_confirmation=True,
        retry_on_verify_failure=True,
        build_action=_build_edit_action,
        verify=_verify_edit,
        compose_success=_compose_edit,
    ),
    "delete_file": ActionCapability(
        intent="delete_file",
        requires_confirmation=True,
        retry_on_verify_failure=True,
        build_action=_build_delete_action,
        verify=_verify_delete,
        compose_success=_compose_delete,
    ),
    "open_file": ActionCapability(
        intent="open_file",
        requires_confirmation=True,
        retry_on_verify_failure=False,
        build_action=_build_open_action,
        verify=_verify_open,
        compose_success=_compose_first_output,
    ),
    "set_reminder": ActionCapability(
        intent="set_reminder",
        requires_confirmation=False,
        retry_on_verify_failure=False,
        build_action=_build_reminder_action,
        verify=_verify_reminder,
        compose_success=_compose_first_output,
        after_success=_emit_schedule_after_success,
    ),
    "run_command": ActionCapability(
        intent="run_command",
        requires_confirmation=True,
        retry_on_verify_failure=False,
        build_action=_build_command_action,
        verify=_verify_command,
        compose_success=_compose_first_output,
    ),
}


async def _run_capability(
    *,
    capability: ActionCapability,
    session_id: str,
    user_request: str,
    slots: SlotExtraction,
    emit: Callable[[Any], None],
    ensure_not_cancelled: Callable[[], None],
    debug_trace: MarkdownDebugTrace | None = None,
) -> ActionHandlingResult:
    action = capability.build_action(session_id, slots)
    _trace(
        debug_trace,
        "动作构建",
        summary=f"已构建 `{capability.intent}` 的待执行动作。",
        data={"intent": capability.intent, "action": action},
    )
    action_to_run = action
    if capability.requires_confirmation:
        _trace(
            debug_trace,
            "等待确认",
            summary="这个动作有副作用，系统会先弹确认面板。",
            data={"title": action.title, "steps": action.steps},
        )
        action_to_run, answer = await _confirm_pending_action(
            session_id=session_id,
            action=action,
            emit=emit,
            ensure_not_cancelled=ensure_not_cancelled,
        )
        _trace(
            debug_trace,
            "确认结果",
            summary="收到用户对执行面板的选择。",
            data={"answer": answer or "", "action": action_to_run},
        )
        if answer == "cancel":
            label = _action_label(action_to_run)
            _record_action_event(
                session_id=session_id,
                user_request=user_request,
                action=action_to_run,
                status="cancelled",
                summary=f"用户取消执行：{label}。",
                observed_effect={"answer": "cancel"},
            )
            return ActionHandlingResult(handled=True, assistant_text=f"已取消这一步：{label}。")
        if answer != "confirm":
            label = _action_label(action_to_run)
            _record_action_event(
                session_id=session_id,
                user_request=user_request,
                action=action_to_run,
                status="blocked",
                summary=f"没有得到明确确认，未执行：{label}。",
                observed_effect={"answer": answer or ""},
            )
            return ActionHandlingResult(handled=True, assistant_text=f"这次没有确认，这一步没有执行：{label}。")

    emit(StateEvent(name="executing_action", scope="execution"))
    max_attempts = 2 if capability.retry_on_verify_failure else 1
    receipt: ExecutionReceipt | None = None
    for attempt in range(1, max_attempts + 1):
        _trace(
            debug_trace,
            "执行工具",
            summary=f"开始第 {attempt} 次执行。",
            data={
                "attempt": attempt,
                "tool_calls": [step.tool_name for step in action_to_run.steps],
                "arguments": [step.arguments for step in action_to_run.steps],
            },
        )
        outputs = await execute_pending_action_steps(action_to_run)
        ensure_not_cancelled()
        _trace(
            debug_trace,
            "工具输出",
            summary="工具执行完成，收到原始结果。",
            data={"attempt": attempt, "outputs": outputs},
        )
        emit(StateEvent(name="verifying_action", scope="execution"))
        receipt = await _maybe_await(capability.verify(session_id, action_to_run, outputs))
        receipt.attempts = attempt
        _trace(
            debug_trace,
            "执行验证",
            summary="已对这次副作用动作做结果验证。",
            data=receipt,
        )
        if receipt.verified or attempt >= max_attempts:
            break
        emit(StateEvent(name="repairing_action", scope="execution"))
        _trace(
            debug_trace,
            "准备重试",
            summary="验证没有通过，准备按能力策略重试一次。",
            data={"attempt": attempt, "failure_reason": receipt.failure_reason or ""},
        )

    if receipt is None:
        return ActionHandlingResult(handled=True, assistant_text="这一步没有产出结果。")

    tools_called = {step.tool_name for step in action_to_run.steps}
    if not receipt.verified:
        failure = receipt.failure_reason or "执行后验证没有通过。"
        _record_action_event(
            session_id=session_id,
            user_request=user_request,
            action=action_to_run,
            status=_event_status_for_failure(failure),
            summary=f"我尝试执行了，但验证没通过：{failure}",
            expected_effect=receipt.expected_effect,
            observed_effect={**receipt.observed_effect, "attempts": receipt.attempts},
        )
        return ActionHandlingResult(
            handled=True,
            assistant_text=f"我尝试执行了，但验证没通过：{failure}",
            tools_called=tools_called,
            receipt=receipt,
        )

    if capability.after_success:
        await _maybe_await(capability.after_success(session_id, action_to_run, receipt, emit))
    success_text = capability.compose_success(action_to_run, receipt)
    _record_action_event(
        session_id=session_id,
        user_request=user_request,
        action=action_to_run,
        status="verified_success",
        summary=success_text,
        expected_effect=receipt.expected_effect,
        observed_effect={**receipt.observed_effect, "attempts": receipt.attempts},
    )
    return ActionHandlingResult(
        handled=True,
        assistant_text=success_text,
        tools_called=tools_called,
        receipt=receipt,
    )


async def maybe_handle_user_action(
    *,
    session_id: str,
    user_message: str,
    emit: Callable[[Any], None],
    ensure_not_cancelled: Callable[[], None],
    debug_trace: MarkdownDebugTrace | None = None,
) -> ActionHandlingResult:
    pending_slot = pending_slot_store.get(session_id)
    route = await classify_action_intent(user_message)
    source_text = user_message
    if pending_slot and route is None:
        route = IntentRoute(intent=pending_slot.intent, source="pending", confidence=1.0)
        source_text = f"{pending_slot.original_request}\n补充信息：{user_message}"
        _trace(
            debug_trace,
            "补问续接",
            summary="检测到这是上一轮补问的继续回答，已沿用待完成动作继续解析。",
            data={"intent": pending_slot.intent, "source_text": source_text},
        )

    if not route:
        _trace(debug_trace, "动作路由", summary="这条请求没有被识别成副作用动作。")
        return ActionHandlingResult()
    _trace(
        debug_trace,
        "动作路由",
        summary="这条请求命中了副作用动作路由。",
        data=route,
    )

    if pending_slot and route.source != "pending" and route.intent != pending_slot.intent:
        pending_slot_store.clear(session_id)

    capability = CAPABILITIES.get(route.intent)
    if not capability:
        _trace(
            debug_trace,
            "能力查找",
            summary=f"找不到 `{route.intent}` 对应的能力实现。",
            data={"intent": route.intent},
        )
        return ActionHandlingResult()

    slots = await extract_action_slots(source_text, route.intent)
    if not slots:
        question = _clarify_missing_slots(route.intent, user_message)
        pending_slot_store.set(
            session_id,
            intent=route.intent,
            original_request=source_text,
            clarification=question,
        )
        _trace(
            debug_trace,
            "参数提取",
            summary=f"`{route.intent}` 的参数提取失败，已转成补问而不是继续盲目执行。",
            data={"intent": route.intent, "clarification": question},
        )
        return ActionHandlingResult(handled=True, assistant_text=question)
    pending_slot_store.clear(session_id)
    _trace(
        debug_trace,
        "参数提取",
        summary=f"已提取 `{route.intent}` 所需参数。",
        data=slots,
    )

    return await _run_capability(
        capability=capability,
        session_id=session_id,
        user_request=user_message,
        slots=slots,
        emit=emit,
        ensure_not_cancelled=ensure_not_cancelled,
        debug_trace=debug_trace,
    )
