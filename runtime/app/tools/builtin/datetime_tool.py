from datetime import datetime
from app.tools.registry import tool

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


@tool
def get_datetime() -> str:
    """获取当前的日期、时间和星期。"""
    now = datetime.now()
    return f"{now.strftime('%Y年%m月%d日')} {_WEEKDAYS[now.weekday()]} {now.strftime('%H:%M:%S')}"
