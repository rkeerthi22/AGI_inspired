"""Canonical UTC timestamps for prediction records."""
from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(at: datetime | None = None) -> str:
    value = at or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_after(hours: float) -> str:
    return utc_iso(utc_now() + timedelta(hours=hours))


def utc_date(offset_days: int = 0) -> str:
    return (utc_now() + timedelta(days=offset_days)).date().isoformat()
