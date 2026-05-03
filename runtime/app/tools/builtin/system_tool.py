import platform
import subprocess
from app.tools.registry import tool


@tool
def open_file(path: str, confirmed: bool = False) -> str:
    """打开文件、文件夹或应用程序。path: 文件路径或应用名称。"""
    try:
        if not confirmed:
            return "需要用户确认后才能打开目标"
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["start", "", path], shell=True)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return f"已打开：{path}"
    except Exception as e:
        return f"打开失败：{e}"
