from __future__ import annotations

import logging
import re
import socket
import textwrap
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .nws_client import Alert
from .timefmt import choose_start_time, format_receipt_datetime, is_effectively_immediate, to_local


ESC = b"\x1b"
GS = b"\x1d"


class PrinterError(RuntimeError):
    """Raised when the thermal printer cannot be reached or written to."""


PUNCTUATION_TRANSLATIONS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "\u2022": "*",
        "\u2192": "->",
    }
)


@dataclass(frozen=True, slots=True)
class ReceiptPreview:
    text: str
    bytes_payload: bytes


def sanitize_text(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(PUNCTUATION_TRANSLATIONS)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(ch for ch in normalized if ch == "\n" or ord(ch) >= 32)
    normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def estimate_chars_per_line(print_width_pixels: int) -> int:
    if print_width_pixels >= 560:
        return 48
    if print_width_pixels >= 500:
        return 42
    if print_width_pixels >= 380:
        return 32
    return max(24, print_width_pixels // 12)


def wrap_text(text: str, width: int) -> list[str]:
    if not text:
        return []

    paragraphs = re.split(r"\n\s*\n", text)
    wrapper = textwrap.TextWrapper(
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=True,
    )

    lines: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        cleaned = " ".join(part.strip() for part in paragraph.splitlines() if part.strip())
        if not cleaned:
            continue
        wrapped = wrapper.wrap(cleaned) or [cleaned]
        lines.extend(wrapped)
        if index < len(paragraphs) - 1:
            lines.append("")
    return lines


class EscPosPrinter:
    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.chars_per_line = estimate_chars_per_line(config.print_width_pixels)

    def print_alert(self, alert: Alert, now: datetime) -> str:
        preview = self.build_receipt(alert, now)
        if self.config.printer_dry_run:
            self.logger.info("Printer dry-run enabled. Receipt preview:\n%s", preview.text)
            return preview.text

        if not self.config.printer_ip:
            raise PrinterError("PRINTER_IP is not configured.")

        try:
            with socket.create_connection(
                (self.config.printer_ip, self.config.printer_port),
                timeout=self.config.printer_timeout_seconds,
            ) as connection:
                connection.settimeout(self.config.printer_timeout_seconds)
                connection.sendall(preview.bytes_payload)
        except OSError as exc:
            raise PrinterError(
                f"Unable to send receipt to printer at {self.config.printer_ip}:{self.config.printer_port}: {exc}"
            ) from exc

        return preview.text

    def build_receipt(self, alert: Alert, now: datetime) -> ReceiptPreview:
        preview_text = self._build_preview_text(alert, now)
        payload = self._build_escpos_bytes(alert, now)
        return ReceiptPreview(text=preview_text, bytes_payload=payload)

    def _build_preview_text(self, alert: Alert, now: datetime) -> str:
        local_now = to_local(now, self.config.zoneinfo) or now
        lines: list[str] = [sanitize_text(alert.event).upper()]

        metadata = self._metadata_line(alert)
        lines.append("-" * self.chars_per_line)
        if metadata:
            lines.append(metadata)
            lines.append("-" * self.chars_per_line)

        start = choose_start_time(alert.onset, alert.effective)
        start_local = to_local(start, self.config.zoneinfo)
        sent_local = to_local(alert.sent, self.config.zoneinfo)
        if start_local and not is_effectively_immediate(
            start_local,
            sent_local,
            local_now,
            self.config.immediate_threshold_seconds,
        ):
            lines.extend(["STARTS", format_receipt_datetime(start_local, local_now), "-" * self.chars_per_line])

        expires_local = to_local(alert.expires, self.config.zoneinfo)
        if expires_local:
            lines.extend(["EXPIRES", format_receipt_datetime(expires_local, local_now), "-" * self.chars_per_line])

        description = sanitize_text(alert.description or alert.headline or "")
        if description:
            lines.append("DESCRIPTION")
            lines.extend(wrap_text(description, self.chars_per_line))
            lines.append("-" * self.chars_per_line)

        instruction = sanitize_text(alert.instruction)
        if instruction:
            lines.append("INSTRUCTION")
            lines.extend(wrap_text(instruction, self.chars_per_line))
            lines.append("-" * self.chars_per_line)

        sender = sanitize_text(alert.sender_name)
        if sender:
            lines.append(sender)

        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _build_escpos_bytes(self, alert: Alert, now: datetime) -> bytes:
        local_now = to_local(now, self.config.zoneinfo) or now
        data = bytearray()
        data.extend(self._initialize())

        self._append_event(data, sanitize_text(alert.event))

        metadata = self._metadata_line(alert)
        self._append_rule(data)
        if metadata:
            self._append_line(data, metadata)
            self._append_rule(data)

        start = choose_start_time(alert.onset, alert.effective)
        start_local = to_local(start, self.config.zoneinfo)
        sent_local = to_local(alert.sent, self.config.zoneinfo)
        if start_local and not is_effectively_immediate(
            start_local,
            sent_local,
            local_now,
            self.config.immediate_threshold_seconds,
        ):
            self._append_section(data, "STARTS", [format_receipt_datetime(start_local, local_now)])

        expires_local = to_local(alert.expires, self.config.zoneinfo)
        if expires_local:
            self._append_section(data, "EXPIRES", [format_receipt_datetime(expires_local, local_now)])

        description = sanitize_text(alert.description or alert.headline or "")
        if description:
            self._append_section(data, "DESCRIPTION", wrap_text(description, self.chars_per_line))

        instruction = sanitize_text(alert.instruction)
        if instruction:
            self._append_section(data, "INSTRUCTION", wrap_text(instruction, self.chars_per_line))

        sender = sanitize_text(alert.sender_name)
        if sender:
            self._set_align(data, 1)
            self._set_emphasis(data, False)
            self._set_size(data, width=1, height=1)
            for line in wrap_text(sender, self.chars_per_line):
                self._write_text(data, f"{line}\n")
            self._set_align(data, 0)

        data.extend(b"\n\n\n")
        if self.config.cut_paper:
            data.extend(GS + b"V\x00")
        return bytes(data)

    def _metadata_line(self, alert: Alert) -> str:
        values = [sanitize_text(alert.severity), sanitize_text(alert.urgency), sanitize_text(alert.certainty)]
        return " | ".join(value.upper() for value in values if value)

    def _append_event(self, data: bytearray, event: str) -> None:
        event = event or "UNKNOWN ALERT"
        large_width = max(12, self.chars_per_line // 2)
        medium_width = max(18, int(self.chars_per_line / 1.5))

        large_lines = wrap_text(event, large_width)
        medium_lines = wrap_text(event, medium_width)

        self._set_align(data, 1)
        self._set_emphasis(data, True)

        if large_lines and len(large_lines) <= 3:
            self._set_size(data, width=2, height=2)
            for line in large_lines:
                if line:
                    self._write_text(data, f"{line}\n")
        elif medium_lines and len(medium_lines) <= 3:
            self._set_size(data, width=2, height=1)
            for line in medium_lines:
                if line:
                    self._write_text(data, f"{line}\n")
        else:
            self._set_size(data, width=1, height=1)
            for line in wrap_text(event, self.chars_per_line):
                if line:
                    self._write_text(data, f"{line}\n")

        self._set_size(data, width=1, height=1)
        self._set_emphasis(data, False)
        self._set_align(data, 0)

    def _append_section(self, data: bytearray, title: str, body_lines: list[str]) -> None:
        self._append_rule(data)
        self._set_emphasis(data, True)
        self._write_text(data, f"{title}\n")
        self._set_emphasis(data, False)
        for line in body_lines:
            self._write_text(data, f"{line}\n")

    def _append_rule(self, data: bytearray) -> None:
        self._write_text(data, f"{'-' * self.chars_per_line}\n")

    def _append_line(self, data: bytearray, text: str) -> None:
        self._write_text(data, f"{text}\n")

    def _initialize(self) -> bytes:
        return ESC + b"@" + ESC + b"a\x00"

    def _set_align(self, data: bytearray, align: int) -> None:
        data.extend(ESC + b"a" + bytes([align]))

    def _set_emphasis(self, data: bytearray, enabled: bool) -> None:
        data.extend(ESC + b"E" + (b"\x01" if enabled else b"\x00"))

    def _set_size(self, data: bytearray, width: int, height: int) -> None:
        width = min(max(width, 1), 8)
        height = min(max(height, 1), 8)
        size_byte = ((width - 1) << 4) | (height - 1)
        data.extend(GS + b"!" + bytes([size_byte]))

    def _write_text(self, data: bytearray, text: str) -> None:
        parts = text.split("\n")
        for index, part in enumerate(parts):
            sanitized = sanitize_text(part)
            if sanitized:
                data.extend(sanitized.encode("ascii", "ignore"))
            if index < len(parts) - 1:
                data.extend(b"\n")
