from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
import unittest

from PIL import Image

from app.config import Config
from app.nws_client import Alert
from app.printing import EscPosPrinter


def sample_config(**overrides) -> Config:
    values = {
        "nws_latitude": 38.8895,
        "nws_longitude": -77.0353,
        "nws_user_agent": "test-agent",
        "nws_accept": "application/geo+json",
        "poll_interval_seconds": 30,
        "http_timeout_seconds": 15.0,
        "http_max_retries": 5,
        "http_backoff_seconds": 2.0,
        "http_max_backoff_seconds": 120.0,
        "timezone": "America/New_York",
        "printer_ip": None,
        "printer_port": 9100,
        "printer_timeout_seconds": 10.0,
        "print_width_pixels": 576,
        "cut_paper": False,
        "printer_dry_run": True,
        "allowed_events": (),
        "blocked_events": (),
        "allowed_severities": (),
        "allowed_urgencies": (),
        "allowed_certainties": (),
        "allowed_statuses": (),
        "allowed_message_types": (),
        "print_on_new_only": True,
        "print_on_updates": False,
        "state_file": Path("/tmp/nws-alerts-state.json"),
        "log_level": "INFO",
        "spoof_alerts_file": None,
        "immediate_threshold_seconds": 120,
    }
    values.update(overrides)
    return Config(**values)


def sample_alert() -> Alert:
    return Alert(
        alert_id="test-alert-1",
        event="Severe Thunderstorm Warning",
        sent=datetime(2026, 4, 22, 18, 11, tzinfo=timezone.utc),
        effective=datetime(2026, 4, 22, 18, 11, tzinfo=timezone.utc),
        onset=datetime(2026, 4, 22, 18, 15, tzinfo=timezone.utc),
        expires=datetime(2026, 4, 22, 19, 0, tzinfo=timezone.utc),
        description=(
            "At 2:11 PM, a severe thunderstorm was located over Fairfax, moving east at 35 mph. "
            "Expect damaging wind gusts and quarter-size hail."
        ),
        instruction=(
            "Move to an interior room on the lowest floor of a sturdy building. "
            "Stay away from windows."
        ),
        severity="Severe",
        certainty="Observed",
        urgency="Immediate",
        status="Actual",
        message_type="Alert",
        sender_name="NWS Baltimore MD/Washington DC",
        headline=None,
    )


class EscPosPrinterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = sample_config()
        self.printer = EscPosPrinter(self.config)
        self.now = datetime(2026, 4, 22, 18, 20, tzinfo=ZoneInfo("America/New_York"))

    def test_preview_uses_morning_brief_style_section_rhythm(self) -> None:
        receipt = self.printer.build_receipt(sample_alert(), now=self.now)

        self.assertEqual(self.printer.chars_per_line, 42)
        self.assertEqual(
            receipt.text,
            "\n".join(
                [
                    "Severe Thunderstorm",
                    "Warning",
                    "------------------------------------------",
                    "STARTS",
                    "2:15 PM",
                    "",
                    "EXPIRES",
                    "3:00 PM",
                    "",
                    "DESCRIPTION",
                    "At 2:11 PM, a severe thunderstorm was",
                    "located over Fairfax, moving east at 35",
                    "mph. Expect damaging wind gusts and",
                    "quarter-size hail.",
                    "",
                    "INSTRUCTION",
                    "Move to an interior room on the lowest",
                    "floor of a sturdy building. Stay away from",
                    "windows.",
                    "",
                    "------------------------------------------",
                    "SEVERE | IMMEDIATE | OBSERVED",
                    "ACTUAL | ALERT",
                    "NWS Baltimore MD/Washington DC",
                    "Sent 2:11 PM",
                    "",
                ]
            ),
        )

    def test_escpos_payload_only_emphasizes_the_header(self) -> None:
        payload = self.printer.build_receipt(sample_alert(), now=self.now).bytes_payload

        self.assertTrue(payload.startswith(b"\x1b@\x1ba\x00"))
        self.assertIn(b"\x1dv0", payload)
        self.assertTrue(payload.endswith(b"\n\n\n"))

    def test_dry_run_saves_rendered_receipt_image(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config = sample_config(state_file=Path(tmpdir) / "state.json")
            printer = EscPosPrinter(config)

            printer.print_alert(sample_alert(), now=self.now)

            preview_dir = Path(tmpdir) / "receipt-previews"
            preview_files = list(preview_dir.glob("*.png"))
            self.assertEqual(len(preview_files), 1)

            with Image.open(preview_files[0]) as image:
                self.assertEqual(image.mode, "1")
                self.assertEqual(image.width, config.print_width_pixels)


if __name__ == "__main__":
    unittest.main()
