from __future__ import annotations

import logging
import re
import socket
import textwrap
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .config import Config
from .nws_client import Alert
from .render_receipt import ReceiptSection, render_receipt_image
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
class ReceiptDocument:
    event: str
    sections: tuple[ReceiptSection, ...]
    footer_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReceiptPreview:
    text: str
    bytes_payload: bytes
    receipt_image: Image.Image


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
    if print_width_pixels >= 500:
        return 42
    if print_width_pixels >= 380:
        return 32
    return max(24, print_width_pixels // 12)


def wrap_text(text: str, width: int) -> list[str]:
    if not text:
        return []

    wrapper = textwrap.TextWrapper(
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=True,
    )
    return wrapper.wrap(text) or [text]


def split_paragraphs(text: str) -> tuple[str, ...]:
    if not text:
        return ()

    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        cleaned = " ".join(part.strip() for part in paragraph.splitlines() if part.strip())
        if cleaned:
            paragraphs.append(cleaned)
    return tuple(paragraphs)


class EscPosPrinter:
    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.chars_per_line = estimate_chars_per_line(config.print_width_pixels)

    def print_alert(self, alert: Alert, now: datetime) -> str:
        preview = self.build_receipt(alert, now)
        if self.config.printer_dry_run:
            image_path = self._save_preview_image(alert, preview.receipt_image, now)
            self.logger.info("Printer dry-run enabled. Receipt preview:\n%s", preview.text)
            self.logger.info("Rendered receipt image saved to %s", image_path)
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
        document = self._build_receipt_document(alert, now)
        preview_text = self._build_preview_text(document)
        receipt_image = render_receipt_image(
            event=document.event,
            sections=document.sections,
            footer_lines=document.footer_lines,
            receipt_width=self.config.print_width_pixels,
        )
        payload = self._build_escpos_bytes(receipt_image)
        return ReceiptPreview(text=preview_text, bytes_payload=payload, receipt_image=receipt_image)

    def _build_receipt_document(self, alert: Alert, now: datetime) -> ReceiptDocument:
        local_now = to_local(now, self.config.zoneinfo) or now
        event = sanitize_text(alert.event) or "Unknown Alert"
        sections = tuple(self._receipt_sections(alert, local_now))
        footer_lines = tuple(self._footer_lines(alert, local_now))
        return ReceiptDocument(event=event, sections=sections, footer_lines=footer_lines)

    def _build_preview_text(self, document: ReceiptDocument) -> str:
        lines: list[str] = list(self._header_lines(document.event))
        lines.append(self._divider())

        for index, section in enumerate(document.sections):
            if index > 0:
                lines.append("")
            lines.append(section.title)
            lines.extend(self._paragraph_lines(section.paragraphs))

        if document.footer_lines:
            if document.sections:
                lines.append("")
            lines.append(self._divider())
            lines.extend(document.footer_lines)

        return "\n".join(lines).strip() + "\n"

    def _divider(self) -> str:
        return "-" * self.chars_per_line

    def _header_lines(self, event: str) -> list[str]:
        large_width = max(12, self.chars_per_line // 2)
        medium_width = max(18, int(self.chars_per_line / 1.5))

        large_lines = wrap_text(event, large_width)
        if len(large_lines) <= 3:
            return large_lines

        medium_lines = wrap_text(event, medium_width)
        if len(medium_lines) <= 3:
            return medium_lines

        return wrap_text(event, self.chars_per_line)

    def _paragraph_lines(self, paragraphs: tuple[str, ...]) -> list[str]:
        lines: list[str] = []
        for index, paragraph in enumerate(paragraphs):
            lines.extend(wrap_text(paragraph, self.chars_per_line))
            if index < len(paragraphs) - 1:
                lines.append("")
        return lines

    def _receipt_sections(self, alert: Alert, local_now: datetime) -> list[ReceiptSection]:
        sections: list[ReceiptSection] = []

        start = choose_start_time(alert.onset, alert.effective)
        start_local = to_local(start, self.config.zoneinfo)
        sent_local = to_local(alert.sent, self.config.zoneinfo)
        if start_local and not is_effectively_immediate(
            start_local,
            sent_local,
            local_now,
            self.config.immediate_threshold_seconds,
        ):
            sections.append(ReceiptSection("STARTS", (format_receipt_datetime(start_local, local_now),)))

        expires_local = to_local(alert.expires, self.config.zoneinfo)
        if expires_local:
            sections.append(ReceiptSection("EXPIRES", (format_receipt_datetime(expires_local, local_now),)))

        description = sanitize_text(alert.description or alert.headline or "")
        if description:
            sections.append(ReceiptSection("DESCRIPTION", split_paragraphs(description)))

        instruction = sanitize_text(alert.instruction)
        if instruction:
            sections.append(ReceiptSection("INSTRUCTION", split_paragraphs(instruction)))

        return sections

    def _metadata_lines(self, alert: Alert) -> list[str]:
        lines: list[str] = []

        headline_values = [sanitize_text(alert.severity), sanitize_text(alert.urgency), sanitize_text(alert.certainty)]
        headline = " | ".join(value.upper() for value in headline_values if value)
        if headline:
            lines.extend(wrap_text(headline, self.chars_per_line))

        status_values = [sanitize_text(alert.status), sanitize_text(alert.message_type)]
        status_line = " | ".join(value.upper() for value in status_values if value)
        if status_line:
            lines.extend(wrap_text(status_line, self.chars_per_line))

        return lines

    def _footer_lines(self, alert: Alert, local_now: datetime) -> list[str]:
        lines = self._metadata_lines(alert)

        sender = sanitize_text(alert.sender_name)
        if sender:
            lines.extend(wrap_text(sender, self.chars_per_line))

        sent_local = to_local(alert.sent, self.config.zoneinfo)
        if sent_local:
            lines.append(f"Sent {format_receipt_datetime(sent_local, local_now)}")

        return lines

    def _save_preview_image(self, alert: Alert, receipt_image: Image.Image, now: datetime) -> Path:
        preview_dir = self.config.state_file.parent / "receipt-previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        timestamp = (alert.sent or now).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_slug = sanitize_text(alert.alert_id or alert.event).lower()
        slug = re.sub(r"[^a-z0-9._-]+", "-", raw_slug).strip("-") or "alert"
        slug = slug[:80]

        path = preview_dir / f"{timestamp}-{slug}.png"
        receipt_image.save(path)
        return path.resolve()

    def _build_escpos_bytes(self, receipt_image: Image.Image) -> bytes:
        data = bytearray()
        data.extend(self._initialize())
        for band in self._iter_raster_bands(receipt_image):
            data.extend(self._raster_band_bytes(band))
        data.extend(b"\n\n\n")
        if self.config.cut_paper:
            data.extend(GS + b"V\x00")
        return bytes(data)

    def _iter_raster_bands(self, receipt_image: Image.Image, band_height: int = 255) -> list[Image.Image]:
        prepared = receipt_image.convert("1")
        width, height = prepared.size
        padded_width = ((width + 7) // 8) * 8
        if padded_width != width:
            padded = Image.new("1", (padded_width, height), 1)
            padded.paste(prepared, (0, 0))
            prepared = padded

        bands: list[Image.Image] = []
        for top in range(0, prepared.height, band_height):
            bands.append(prepared.crop((0, top, prepared.width, min(top + band_height, prepared.height))))
        return bands

    def _raster_band_bytes(self, band: Image.Image) -> bytes:
        width, height = band.size
        width_bytes = width // 8
        raster = bytearray()

        for y in range(height):
            for byte_index in range(width_bytes):
                value = 0
                for bit in range(8):
                    x = (byte_index * 8) + bit
                    if band.getpixel((x, y)) == 0:
                        value |= 0x80 >> bit
                raster.append(value)

        x_low = width_bytes & 0xFF
        x_high = (width_bytes >> 8) & 0xFF
        y_low = height & 0xFF
        y_high = (height >> 8) & 0xFF
        return GS + b"v0" + bytes([0, x_low, x_high, y_low, y_high]) + raster

    def _initialize(self) -> bytes:
        return ESC + b"@" + ESC + b"a\x00"
