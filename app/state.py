from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .nws_client import Alert


class StateError(RuntimeError):
    """Raised when persisted dedupe state cannot be loaded or saved."""


@dataclass(frozen=True, slots=True)
class DedupeDecision:
    should_print: bool
    reason: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict[str, object]] = {}
        self._load()

    def plan(self, alert: Alert, print_on_new_only: bool, print_on_updates: bool) -> DedupeDecision:
        record = self.records.get(alert.alert_id)
        fingerprint = alert.meaningful_fingerprint

        if not print_on_new_only:
            return DedupeDecision(True, "PRINT_ON_NEW_ONLY=false, printing every qualifying poll")

        if record is None:
            return DedupeDecision(True, "first time alert id has been seen")

        fingerprints = set(self._fingerprints_for(record))
        if print_on_updates and fingerprint not in fingerprints:
            return DedupeDecision(True, "alert content changed and PRINT_ON_UPDATES=true")

        if print_on_updates:
            return DedupeDecision(False, "same alert content already handled")

        return DedupeDecision(False, "alert id already handled and PRINT_ON_UPDATES=false")

    def record_attempt(self, alert: Alert) -> None:
        record = self.records.setdefault(alert.alert_id, {})
        now = datetime.now(timezone.utc).isoformat()
        fingerprints = self._fingerprints_for(record)
        if alert.meaningful_fingerprint not in fingerprints:
            fingerprints.append(alert.meaningful_fingerprint)

        record.update(
            {
                "event": alert.event,
                "fingerprints": fingerprints,
                "first_handled_at": record.get("first_handled_at", now),
                "last_attempted_at": now,
                "last_status": "attempted",
                "last_sender_name": alert.sender_name,
            }
        )
        self._save()

    def record_success(self, alert: Alert) -> None:
        record = self.records.setdefault(alert.alert_id, {})
        record.update(
            {
                "event": alert.event,
                "last_status": "printed",
                "last_printed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save()

    def record_failure(self, alert: Alert, error: str) -> None:
        record = self.records.setdefault(alert.alert_id, {})
        record.update(
            {
                "event": alert.event,
                "last_status": "printer_failed",
                "last_error": error,
                "last_failure_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save()

    def _fingerprints_for(self, record: dict[str, object]) -> list[str]:
        value = record.get("fingerprints")
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    def _load(self) -> None:
        if not self.path.exists():
            self.records = {}
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"State file is not valid JSON: {self.path}") from exc
        except OSError as exc:
            raise StateError(f"Unable to read state file: {self.path}") from exc

        version = payload.get("version")
        if version != 1:
            raise StateError(f"Unsupported state file version {version!r} in {self.path}")

        records = payload.get("records")
        if not isinstance(records, dict):
            raise StateError(f"State file records payload is invalid: {self.path}")

        self.records = {str(key): value for key, value in records.items() if isinstance(value, dict)}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "records": self.records}

        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                temp_path = Path(handle.name)
            temp_path.replace(self.path)
        except OSError as exc:
            raise StateError(f"Unable to write state file: {self.path}") from exc
