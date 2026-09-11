"""Canonical UTC timestamps shared by normal writes and schema migration."""

from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def due_date(borrowed_at: str, days: int) -> str:
    borrowed = datetime.fromisoformat(borrowed_at)
    if borrowed.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return (borrowed.astimezone(timezone.utc) + timedelta(days=days)).isoformat(timespec="microseconds")
