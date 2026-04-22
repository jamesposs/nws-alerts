from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .nws_client import Alert


@dataclass(frozen=True, slots=True)
class FilterResult:
    should_print: bool
    reason: str


def evaluate_alert(alert: Alert, config: Config) -> FilterResult:
    event = _normalize(alert.event)
    if config.blocked_events and event in _normalized_set(config.blocked_events):
        return FilterResult(False, f"blocked event: {alert.event}")

    checks = (
        ("event", alert.event, config.allowed_events),
        ("severity", alert.severity, config.allowed_severities),
        ("urgency", alert.urgency, config.allowed_urgencies),
        ("certainty", alert.certainty, config.allowed_certainties),
        ("status", alert.status, config.allowed_statuses),
        ("messageType", alert.message_type, config.allowed_message_types),
    )

    for label, value, allowlist in checks:
        if not allowlist:
            continue

        normalized_value = _normalize(value)
        if normalized_value not in _normalized_set(allowlist):
            printable = value if value not in (None, "") else "(missing)"
            return FilterResult(False, f"{label} {printable!r} not in allowlist")

    return FilterResult(True, "matched configured filters")


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def _normalized_set(values: tuple[str, ...]) -> set[str]:
    return {value.strip().casefold() for value in values}
