# -*- coding: utf-8 -*-
"""CLI command tool — restricted non-shell command execution with confirmation."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess

from app.tools.registry import tool

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CWD = _REPO_ROOT
_ALLOWED_CWD_ROOTS = (
    _REPO_ROOT,
)
_DISALLOWED_SNIPPETS = (
    "&&", "||", "|", ";", ">", "<", "$(", "`",
)
_BLOCKED_PATTERNS = (
    "sudo ",
    "rm ",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "diskutil eraseDisk",
    "mkfs",
    "dd if=",
    "git reset --hard",
    "git clean -fd",
    "chmod -R 777 /",
    "chown -R /",
)
_TIMEOUT = 20
_MAX_OUTPUT = 6000
_READ_ONLY_COMMANDS = {
    "pwd", "ls", "rg", "cat", "head", "tail", "wc", "find", "ps", "lsof",
}
_ALLOWED_GIT_SUBCOMMANDS = {
    "status", "diff", "log", "show", "branch", "rev-parse",
}
_ALLOWED_NPM_RUN_SCRIPTS = {"dev", "build", "test"}
_ALLOWED_CURL_HOSTS = ("http://127.0.0.1", "http://localhost")


def _resolve_cwd(raw: str | None) -> Path:
    if not raw:
        return _DEFAULT_CWD.resolve()
    text = os.path.expanduser(os.path.expandvars(raw)).strip().replace("\\", "/")
    if text in {"repo", "project", "workspace", "."}:
        candidate = _DEFAULT_CWD
    else:
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = (_DEFAULT_CWD / candidate).resolve()
    candidate = candidate.resolve()
    for root in _ALLOWED_CWD_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            return candidate
        except ValueError:
            continue
    raise ValueError(f"工作目录不在允许范围内：{candidate}")


def _is_allowed_command(args: list[str]) -> bool:
    if not args:
        return False
    exe = Path(args[0]).name

    if exe in _READ_ONLY_COMMANDS:
        return True

    if exe == "git":
        return len(args) >= 2 and args[1] in _ALLOWED_GIT_SUBCOMMANDS

    if exe == "npm":
        return len(args) >= 3 and args[1] == "run" and args[2] in _ALLOWED_NPM_RUN_SCRIPTS

    if exe == "uvicorn":
        return True

    if exe in {"python", "python3"}:
        return len(args) >= 3 and args[1] == "-m" and args[2] == "uvicorn"

    if exe == "curl":
        urls = [arg for arg in args[1:] if arg.startswith(("http://", "https://"))]
        return bool(urls) and all(url.startswith(_ALLOWED_CURL_HOSTS) for url in urls)

    return False


def _token_may_be_path(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    if token.startswith(("http://", "https://")):
        return False
    if ":" in token and not token.startswith(("/", "./", "../", "~")):
        return False
    return token.startswith(("/", "./", "../", "~")) or "/" in token


def _validate_path_token(token: str, workdir: Path) -> bool:
    if not _token_may_be_path(token):
        return True
    candidate = Path(os.path.expanduser(token))
    if not candidate.is_absolute():
        candidate = (workdir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(_REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def _validate_command(command: str, workdir: Path) -> tuple[str | None, list[str] | None]:
    lowered = command.lower()
    if any(snippet in command for snippet in _DISALLOWED_SNIPPETS):
        return "安全策略拒绝：暂不支持管道、重定向、命令连接或 shell 替换，请拆成单条命令", None
    if any(pattern in lowered for pattern in _BLOCKED_PATTERNS):
        return "安全策略拒绝：这条命令风险过高，不能直接执行", None
    try:
        args = shlex.split(command, posix=True)
    except ValueError as e:
        return f"命令解析失败：{e}", None
    if not args:
        return "命令为空，无法执行", None
    if not _is_allowed_command(args):
        return "安全策略拒绝：当前 CLI 只允许项目内只读命令和受控启动命令", None
    if not all(_validate_path_token(token, workdir) for token in args[1:]):
        return "安全策略拒绝：命令参数里的路径必须限制在当前项目内", None
    return None, args


def _format_output(result: subprocess.CompletedProcess[str]) -> str:
    stdout = (result.stdout or "")[:_MAX_OUTPUT].rstrip()
    stderr = (result.stderr or "")[:_MAX_OUTPUT].rstrip()
    parts: list[str] = []
    if stdout:
        parts.append(f"[stdout]\n{stdout}")
    if stderr:
        parts.append(f"[stderr]\n{stderr}")

    if result.returncode == 0:
        if parts:
            return "命令已执行（退出码 0）\n" + "\n".join(parts)
        return "命令已执行（退出码 0，无输出）"
    if parts:
        return f"命令执行失败（退出码 {result.returncode}）\n" + "\n".join(parts)
    return f"命令执行失败（退出码 {result.returncode}）"


@tool
def run_command(command: str, cwd: str = "", confirmed: bool = False) -> str:
    """执行一条非交互 CLI 命令。command: 单条命令；cwd: 可选工作目录。默认在项目根目录执行。"""
    try:
        if not confirmed:
            return "需要用户确认后才能执行命令"
        workdir = _resolve_cwd(cwd)
        reason, args = _validate_command(command, workdir)
        if reason:
            return reason
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(workdir),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return _format_output(result)
    except ValueError as e:
        return f"权限错误：{e}"
    except subprocess.TimeoutExpired:
        return f"执行超时（>{_TIMEOUT}s）"
    except FileNotFoundError:
        return "命令不存在，请检查可执行文件名或环境变量"
    except Exception as e:
        return f"命令执行错误：{e}"
