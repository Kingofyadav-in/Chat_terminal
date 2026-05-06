import time
import uuid
import datetime


def now() -> int:
    return int(time.time())


def new_id() -> str:
    return str(uuid.uuid4())


def fmt_time(ts: int) -> str:
    dt = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if dt.date() == today:
        return dt.strftime("%I:%M %p").lstrip("0")
    yesterday = today - datetime.timedelta(days=1)
    if dt.date() == yesterday:
        return "Yesterday " + dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%b %d %I:%M %p")


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
