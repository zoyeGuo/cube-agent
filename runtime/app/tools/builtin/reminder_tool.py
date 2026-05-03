# -*- coding: utf-8 -*-
"""Reminder tool — schedule a one-off push notification for the user."""
import logging
import re
from datetime import datetime, timedelta

import dateparser

from app.tools.registry import tool
from app.tools.context import current_session_id

_log = logging.getLogger(__name__)


def _normalize_reminder_time(time_str: str) -> str:
    cleaned = time_str.strip()
    cleaned = cleaned.strip("。！？!?,， ")
    cleaned = "".join(cleaned.split())
    cleaned = cleaned.replace("个", "")
    cleaned = cleaned.replace("分鐘", "分钟")
    cleaned = cleaned.replace("小時", "小时")
    cleaned = cleaned.replace("鐘頭", "小时")
    cleaned = cleaned.replace("之后", "后")
    cleaned = cleaned.replace("之後", "后")
    cleaned = cleaned.replace("后的", "后")
    cleaned = cleaned.replace("後的", "后")
    if cleaned.endswith("提醒"):
        cleaned = cleaned[:-2]
    return cleaned


def _parse_simple_number(token: str) -> float | None:
    token = token.strip()
    if not token:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    if token == "半":
        return 0.5

    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    total = 0
    current = 0
    for char in token:
        if char in digits:
            current = digits[char]
            continue
        if char in units:
            total += (current or 1) * units[char]
            current = 0
            continue
        return None
    return float(total + current)


def _parse_relative_time(time_str: str) -> datetime | None:
    match = re.fullmatch(r"(?P<num>[0-9一二两三四五六七八九十百千万半\.]+)(?P<unit>分钟|小时|天)后", time_str)
    if not match:
        return None

    value = _parse_simple_number(match.group("num"))
    if value is None:
        return None

    unit = match.group("unit")
    now = dateparser.parse(
        "现在",
        languages=["zh"],
        settings={
            "TIMEZONE": "Asia/Shanghai",
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if not now:
        now = datetime.now().astimezone()

    if unit == "分钟":
        return now + timedelta(minutes=value)
    if unit == "小时":
        return now + timedelta(hours=value)
    return now + timedelta(days=value)


@tool
async def set_reminder(time_str: str, message: str) -> str:
    """在指定时间向用户发送提醒。time_str 支持自然语言，如'明天早上8点'、'30分钟后'、'2026-04-21 09:00'。"""
    session_id = current_session_id.get()
    if not session_id:
        return "错误：无法获取当前会话 ID，提醒设置失败"

    normalized_time = _normalize_reminder_time(time_str)

    run_date = dateparser.parse(
        normalized_time,
        languages=["zh"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": "Asia/Shanghai",
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if not run_date:
        run_date = _parse_relative_time(normalized_time)
    if not run_date:
        return "无法解析时间，请换一种说法，例如'明天早上8点'或'30分钟后'"

    if run_date <= datetime.now(run_date.tzinfo):
        return f"时间 {run_date.strftime('%Y-%m-%d %H:%M')} 已过去，请指定未来的时间"

    from app.services.scheduler import scheduler, fire_reminder
    scheduler.add_job(
        fire_reminder,
        trigger="date",
        run_date=run_date,
        args=[session_id, message],
        misfire_grace_time=60,
    )

    _log.info("reminder set: session=%s raw_time=%s normalized_time=%s time=%s msg=%s", session_id, time_str, normalized_time, run_date, message)
    return f"提醒已设置：将在 {run_date.strftime('%Y年%m月%d日 %H:%M')} 提醒你\"{message}\""
