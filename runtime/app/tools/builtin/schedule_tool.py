# -*- coding: utf-8 -*-
"""show_schedule_panel tool — trigger the frontend schedule panel."""
from app.tools.registry import tool

_SENTINEL = "__SHOW_SCHEDULE__"


@tool
async def show_schedule_panel() -> str:
    """显示用户的日程和提醒面板。当用户询问今天有什么安排、有哪些提醒、有什么任务时调用。"""
    return _SENTINEL
