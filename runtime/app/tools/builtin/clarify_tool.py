# -*- coding: utf-8 -*-
"""ask_user tool — pause execution and request clarification from the user."""
from app.tools.registry import tool

_SENTINEL = "__ASK_USER__:"


@tool
async def ask_user(question: str) -> str:
    """当任务存在歧义、缺少关键信息或需要用户确认时，向用户提问。
    调用后执行将暂停，直到用户回复。question: 要问用户的具体问题。"""
    return f"{_SENTINEL}{question}"
