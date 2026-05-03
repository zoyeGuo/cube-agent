# -*- coding: utf-8 -*-
"""Python code execution sandbox — subprocess isolation with timeout."""
import subprocess
import sys
import textwrap
import tempfile
import os
from pathlib import Path
import re

from app.tools.registry import tool

_TIMEOUT = 15        # 秒
_MAX_OUTPUT = 4096   # 输出字符上限

# 禁止导入的危险模块
_BLOCKED = {
    "subprocess", "os.system", "shutil.rmtree",
    "socket", "ctypes", "multiprocessing",
}
_FORBIDDEN_SIDE_EFFECT_PATTERNS = (
    r"\bopen\s*\(",
    r"\.open\s*\(",
    r"\.write_text\s*\(",
    r"\.write_bytes\s*\(",
    r"\.touch\s*\(",
    r"\.mkdir\s*\(",
    r"\bunlink\s*\(",
    r"\brename\s*\(",
    r"\breplace\s*\(",
    r"\bos\.remove\s*\(",
    r"\bos\.rename\s*\(",
    r"\bos\.makedirs\s*\(",
    r"\bos\.unlink\s*\(",
    r"\bshutil\.",
)

_PRELUDE = textwrap.dedent("""\
    import sys, os
    # 限制危险操作
    _orig_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
    _blocked = {"subprocess", "socket", "ctypes", "multiprocessing"}
    def _safe_import(name, *args, **kwargs):
        if name in _blocked:
            raise ImportError(f"模块 '{name}' 在沙箱中不可用")
        return _orig_import(name, *args, **kwargs)
    __builtins__.__import__ = _safe_import
""")


@tool
async def execute_python(code: str, confirmed: bool = False) -> str:
    """在沙箱中执行 Python 代码并返回输出。适合数据处理、计算、文本操作等任务。
    不可访问网络、不可执行系统命令。超时限制 15 秒。"""
    # 基础静态检查
    for blocked in ("import subprocess", "import socket", "import ctypes",
                    "os.system", "os.popen", "shutil.rmtree", "eval(", "exec("):
        if blocked in code:
            return f"沙箱拒绝：代码包含不允许的操作 '{blocked}'"
    if any(re.search(pattern, code) for pattern in _FORBIDDEN_SIDE_EFFECT_PATTERNS):
        return "沙箱拒绝：execute_python 只允许只读计算和文本处理；文件或目录修改请改用专用工具"

    full_code = _PRELUDE + "\n" + code

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", encoding="utf-8", delete=False
        ) as f:
            f.write(full_code)
            tmp_path = f.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(Path.home()),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        stdout = result.stdout[:_MAX_OUTPUT]
        stderr = result.stderr[:_MAX_OUTPUT]

        parts = []
        if stdout:
            parts.append(stdout.rstrip())
        if stderr:
            # 过滤掉 prelude 的行号偏移
            filtered = "\n".join(
                line for line in stderr.splitlines()
                if "tmp" not in line.lower() or "Error" in line
            )
            if filtered:
                parts.append(f"[stderr]\n{filtered}")
        if result.returncode != 0 and not parts:
            parts.append(f"退出码：{result.returncode}")

        return "\n".join(parts) if parts else "（无输出）"

    except subprocess.TimeoutExpired:
        return f"执行超时（>{_TIMEOUT}s），请优化代码或拆分任务"
    except Exception as e:
        return f"执行错误：{e}"
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
