from app.tools.registry import tool


@tool
async def show_voice_panel() -> str:
    """当用户想更换、选择或调整声音/音色时调用此工具。调用后系统会自动弹出音色选择面板。"""
    return "__SHOW_VOICE_PANEL__"
