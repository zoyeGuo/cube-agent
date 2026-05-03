# -*- coding: utf-8 -*-
"""File read/write tools — restricted to user home and desktop."""
import os
from pathlib import Path

from app.tools.registry import tool

# 允许访问的根目录白名单
_ALLOWED_ROOTS = [
    Path.home(),
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path("F:/"),  # 项目盘，可根据实际情况调整
]

_MAX_READ_BYTES = 64 * 1024   # 64 KB 上限，防止读巨大文件塞满 context
_MAX_WRITE_BYTES = 256 * 1024  # 256 KB 写入上限


def _normalize_special_path(raw: str) -> Path:
    text = os.path.expanduser(os.path.expandvars(raw)).strip().replace("\\", "/")
    home = Path.home()
    desktop = home / "Desktop"

    if text in {"Desktop", "desktop", "桌面", "~/Desktop", "~/桌面"}:
        return desktop

    if text.startswith(("Desktop/", "desktop/", "桌面/")):
        return desktop / text.split("/", 1)[1]

    for marker in ("/Desktop/", "/桌面/"):
        if marker in text:
            return desktop / text.split(marker, 1)[1]

    if text.endswith(("/Desktop", "/桌面")):
        return desktop

    return Path(text)


def _safe_path(raw: str) -> Path:
    """解析并验证路径在白名单内，否则抛出 ValueError。"""
    p = _normalize_special_path(raw).resolve()
    for root in _ALLOWED_ROOTS:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    raise ValueError(f"路径不在允许范围内：{p}")


def inspect_text_file(path: str) -> dict:
    """Structured helper for deterministic read_file responses."""
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"ok": False, "path": str(p), "error": f"文件不存在：{p}"}
        if not p.is_file():
            return {"ok": False, "path": str(p), "error": f"不是文件：{p}"}
        size = p.stat().st_size
        if size > _MAX_READ_BYTES:
            return {
                "ok": False,
                "path": str(p),
                "error": f"文件过大（{size // 1024} KB），超过 64 KB 上限，请指定更小的文件",
            }
        return {
            "ok": True,
            "path": str(p),
            "content": p.read_text(encoding="utf-8", errors="replace"),
            "size_bytes": size,
        }
    except ValueError as e:
        return {"ok": False, "path": path, "error": f"权限错误：{e}"}
    except Exception as e:
        return {"ok": False, "path": path, "error": f"读取失败：{e}"}


def inspect_directory(path: str, limit: int = 100) -> dict:
    """Structured helper for deterministic list_directory responses."""
    try:
        p = _safe_path(path)
        if not p.exists():
            return {"ok": False, "path": str(p), "error": f"目录不存在：{p}"}
        if not p.is_dir():
            return {"ok": False, "path": str(p), "error": f"不是目录：{p}"}
        all_entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        entries = []
        for entry in all_entries[:limit]:
            entries.append(
                {
                    "name": entry.name,
                    "kind": "file" if entry.is_file() else "directory",
                    "size_bytes": entry.stat().st_size if entry.is_file() else None,
                }
            )
        return {
            "ok": True,
            "path": str(p),
            "entries": entries,
            "total_count": len(all_entries),
            "truncated": len(all_entries) > limit,
        }
    except ValueError as e:
        return {"ok": False, "path": path, "error": f"权限错误：{e}"}
    except Exception as e:
        return {"ok": False, "path": path, "error": f"列出失败：{e}"}


@tool
def read_file(path: str) -> str:
    """读取文本文件内容。path: 文件路径（支持 ~ 和环境变量）。"""
    result = inspect_text_file(path)
    if not result["ok"]:
        return result["error"]
    return result["content"]


@tool
def write_file(path: str, content: str, mode: str = "overwrite", confirmed: bool = False) -> str:
    """写入文本文件。path: 文件路径；content: 文件内容；mode: 'overwrite'（覆盖）或 'append'（追加）。"""
    try:
        if not confirmed:
            return "需要用户确认后才能写入文件"
        p = _safe_path(path)
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return f"内容过大，超过 256 KB 上限"
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
            return f"已追加写入：{p}"
        else:
            p.write_text(content, encoding="utf-8")
            return f"已写入：{p}"
    except ValueError as e:
        return f"权限错误：{e}"
    except Exception as e:
        return f"写入失败：{e}"


@tool
def edit_file(path: str, new_content: str, mode: str = "overwrite", confirmed: bool = False) -> str:
    """修改已有文本文件内容。path: 文件路径；new_content: 新的完整文件内容；mode 目前仅支持 overwrite。"""
    try:
        if not confirmed:
            return "需要用户确认后才能修改文件"
        if mode != "overwrite":
            return f"修改失败：暂不支持 mode={mode}"
        p = _safe_path(path)
        if not p.exists():
            return f"文件不存在：{p}"
        if not p.is_file():
            return f"不是文件：{p}"
        if len(new_content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return "内容过大，超过 256 KB 上限"
        p.write_text(new_content, encoding="utf-8")
        return f"已修改：{p}"
    except ValueError as e:
        return f"权限错误：{e}"
    except Exception as e:
        return f"修改失败：{e}"


@tool
def delete_file(path: str, confirmed: bool = False) -> str:
    """删除文件。path: 文件路径。"""
    try:
        if not confirmed:
            return "需要用户确认后才能删除文件"
        p = _safe_path(path)
        if not p.exists():
            return f"文件不存在：{p}"
        if not p.is_file():
            return f"不是文件：{p}"
        p.unlink()
        return f"已删除：{p}"
    except ValueError as e:
        return f"权限错误：{e}"
    except Exception as e:
        return f"删除失败：{e}"


@tool
def list_directory(path: str) -> str:
    """列出目录内容。path: 目录路径（支持 ~ 和环境变量）。"""
    result = inspect_directory(path)
    if not result["ok"]:
        return result["error"]
    entries = result["entries"]
    lines = []
    for entry in entries:
        kind = "文件" if entry["kind"] == "file" else "目录"
        size = f"  {entry['size_bytes'] // 1024} KB" if entry["kind"] == "file" else ""
        lines.append(f"[{kind}] {entry['name']}{size}")
    if result["truncated"]:
        lines.append("...（超过 100 条，仅显示前 100）")
    return "\n".join(lines) if lines else "（空目录）"
