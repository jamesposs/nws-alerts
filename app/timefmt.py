from __future__ import annotations

from datetime import datetime


def parse_nws_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    return datetime.fromisoformat(normalized)


def to_local(dt: datetime | None, tzinfo) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(tzinfo)


def choose_start_time(onset: datetime | None, effective: datetime | None) -> datetime | None:
    return onset or effective


def is_effectively_immediate(
    start: datetime | None,
    sent: datetime | None,
    now: datetime,
    threshold_seconds: int,
) -> bool:
    if start is None:
        return True

    if abs((start - now).total_seconds()) <= threshold_seconds:
        return True

    if sent and abs((start - sent).total_seconds()) <= threshold_seconds:
        return True

    return False


def format_receipt_datetime(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "Unknown"

    if dt.date() == now.date():
        return dt.strftime("%I:%M %p").lstrip("0")

    return dt.strftime("%a %b %d %I:%M %p").replace(" 0", " ").lstrip("0")
