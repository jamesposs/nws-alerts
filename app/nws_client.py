from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from .config import Config
from .timefmt import parse_nws_timestamp


class NwsClientError(RuntimeError):
    """Raised when the NWS API cannot be queried successfully."""


@dataclass(frozen=True, slots=True)
class Alert:
    alert_id: str
    event: str
    sent: datetime | None
    effective: datetime | None
    onset: datetime | None
    expires: datetime | None
    description: str | None
    instruction: str | None
    severity: str | None
    certainty: str | None
    urgency: str | None
    status: str | None
    message_type: str | None
    sender_name: str | None
    headline: str | None

    @classmethod
    def from_feature(cls, feature: dict[str, object]) -> "Alert":
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}

        feature_id = str(feature.get("id") or properties.get("id") or "").strip()
        event = _clean_text(properties.get("event")) or "Unknown Alert"
        sent = parse_nws_timestamp(_clean_text(properties.get("sent")))
        effective = parse_nws_timestamp(_clean_text(properties.get("effective")))
        onset = parse_nws_timestamp(_clean_text(properties.get("onset")))
        expires = parse_nws_timestamp(_clean_text(properties.get("expires")))

        dedupe_seed = feature_id or json.dumps(
            {
                "event": event,
                "effective": effective.isoformat() if effective else None,
                "onset": onset.isoformat() if onset else None,
                "expires": expires.isoformat() if expires else None,
            },
            sort_keys=True,
        )
        alert_id = feature_id or f"generated:{hashlib.sha256(dedupe_seed.encode('utf-8')).hexdigest()}"

        return cls(
            alert_id=alert_id,
            event=event,
            sent=sent,
            effective=effective,
            onset=onset,
            expires=expires,
            description=_clean_text(properties.get("description")),
            instruction=_clean_text(properties.get("instruction")),
            severity=_clean_text(properties.get("severity")),
            certainty=_clean_text(properties.get("certainty")),
            urgency=_clean_text(properties.get("urgency")),
            status=_clean_text(properties.get("status")),
            message_type=_clean_text(properties.get("messageType")),
            sender_name=_clean_text(properties.get("senderName")),
            headline=_clean_text(properties.get("headline")),
        )

    @property
    def meaningful_fingerprint(self) -> str:
        payload = {
            "event": self.event,
            "effective": self.effective.isoformat() if self.effective else None,
            "onset": self.onset.isoformat() if self.onset else None,
            "expires": self.expires.isoformat() if self.expires else None,
            "description": self.description,
            "instruction": self.instruction,
            "severity": self.severity,
            "certainty": self.certainty,
            "urgency": self.urgency,
            "status": self.status,
            "message_type": self.message_type,
            "sender_name": self.sender_name,
            "headline": self.headline,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def sort_time(self) -> datetime:
        return self.sent or self.effective or self.onset or self.expires or datetime.now(timezone.utc)


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class NwsClient:
    BASE_URL = "https://api.weather.gov/alerts/active"

    def __init__(self, config: Config, session: requests.Session | None = None, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.logger = logger or logging.getLogger(__name__)
        self.session.headers.update(
            {
                "User-Agent": self.config.nws_user_agent,
                "Accept": self.config.nws_accept,
            }
        )

    def fetch_active_alerts(self) -> list[Alert]:
        if self.config.spoof_alerts_file:
            payload = self._load_spoof_payload(self.config.spoof_alerts_file)
        else:
            payload = self._fetch_live_payload()

        features = payload.get("features") or []
        if not isinstance(features, list):
            raise NwsClientError("NWS response did not contain a GeoJSON features list.")

        alerts = [Alert.from_feature(feature) for feature in features if isinstance(feature, dict)]
        alerts.sort(key=lambda alert: alert.sort_time)
        return alerts

    def _fetch_live_payload(self) -> dict[str, object]:
        params = {"point": self.config.point}

        for attempt in range(1, self.config.http_max_retries + 1):
            try:
                response = self.session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=self.config.http_timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt >= self.config.http_max_retries:
                    raise NwsClientError(f"NWS request failed after {attempt} attempts: {exc}") from exc
                delay = self._compute_backoff(attempt=attempt)
                self.logger.warning(
                    "NWS request attempt %s/%s failed: %s. Retrying in %.1fs.",
                    attempt,
                    self.config.http_max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise NwsClientError("NWS response was not valid JSON.") from exc

            body_excerpt = response.text.strip().replace("\n", " ")[:200]
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt >= self.config.http_max_retries:
                    raise NwsClientError(
                        f"NWS returned HTTP {response.status_code} after {attempt} attempts: {body_excerpt}"
                    )
                delay = self._retry_after_seconds(response.headers.get("Retry-After")) or self._compute_backoff(
                    attempt=attempt,
                )
                self.logger.warning(
                    "NWS returned HTTP %s on attempt %s/%s. Retrying in %.1fs. Body: %s",
                    response.status_code,
                    attempt,
                    self.config.http_max_retries,
                    delay,
                    body_excerpt,
                )
                time.sleep(delay)
                continue

            raise NwsClientError(f"NWS returned HTTP {response.status_code}: {body_excerpt}")

        raise NwsClientError("NWS request failed unexpectedly.")

    def _load_spoof_payload(self, path: Path) -> dict[str, object]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise NwsClientError(f"Spoof alerts file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise NwsClientError(f"Spoof alerts file is not valid JSON: {path}") from exc

        if isinstance(raw, dict) and "features" in raw:
            return raw
        if isinstance(raw, list):
            return {"type": "FeatureCollection", "features": raw}
        if isinstance(raw, dict) and "properties" in raw:
            return {"type": "FeatureCollection", "features": [raw]}
        raise NwsClientError("Spoof alerts file must contain GeoJSON features or a FeatureCollection.")

    def _compute_backoff(self, attempt: int) -> float:
        base_delay = self.config.http_backoff_seconds * (2 ** max(0, attempt - 1))
        jitter = random.uniform(0.0, 0.5)
        return min(base_delay + jitter, self.config.http_max_backoff_seconds)

    def _retry_after_seconds(self, value: str | None) -> float | None:
        if not value:
            return None

        stripped = value.strip()
        if stripped.isdigit():
            return min(float(stripped), self.config.http_max_backoff_seconds)

        try:
            retry_at = parsedate_to_datetime(stripped)
        except (TypeError, ValueError):
            return None

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)

        delta = (retry_at - datetime.now(timezone.utc)).total_seconds()
        if delta <= 0:
            return None
        return min(delta, self.config.http_max_backoff_seconds)
