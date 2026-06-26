from datetime import datetime, timezone, timedelta


def utc_now():
    """返回北京时间 (UTC+8) 的当前时间，统一项目内时间来源。"""

    return datetime.now(timezone(timedelta(hours=8)))
