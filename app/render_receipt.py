"""Render Morning Brief-style alert receipts with Pillow."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE_RECEIPT_WIDTH = 576

FONT_CANDIDATES = {
    "regular": (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "bold": (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}


@dataclass(frozen=True, slots=True)
class ReceiptSection:
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LayoutMetrics:
    receipt_width: int
    page_margin_x: int
    page_margin_y: int
    card_padding_x: int
    card_padding_y: int
    card_gap: int
    card_radius: int
    body_line_spacing: int
    paragraph_gap: int
    section_title_gap: int
    header_gap_after: int
    divider_gap_after: int
    footer_gap_after_divider: int
    footer_line_gap: int

    @classmethod
    def for_width(cls, receipt_width: int) -> "LayoutMetrics":
        scale = receipt_width / BASE_RECEIPT_WIDTH

        def px(value: int, minimum: int = 1) -> int:
            return max(minimum, int(round(value * scale)))

        return cls(
            receipt_width=receipt_width,
            page_margin_x=px(24),
            page_margin_y=px(28),
            card_padding_x=px(24),
            card_padding_y=px(22),
            card_gap=px(18),
            card_radius=px(18),
            body_line_spacing=px(6),
            paragraph_gap=px(14),
            section_title_gap=px(14),
            header_gap_after=px(22),
            divider_gap_after=px(18),
            footer_gap_after_divider=px(14),
            footer_line_gap=px(6),
        )


def _line_height(font: ImageFont.ImageFont, extra: int = 0) -> int:
    bbox = font.getbbox("Ag")
    return (bbox[3] - bbox[1]) + extra


@lru_cache(maxsize=32)
def load_font(style: str, size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES.get(style, ()):
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def prepare_receipt_image(image: Image.Image) -> Image.Image:
    grayscale = ImageOps.autocontrast(image.convert("L"))
    return grayscale.point(lambda value: 0 if value < 200 else 255, mode="1")


def _draw_centered_text(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.ImageFont, width: int) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (width - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, font=font, fill=0)
    return y + (bbox[3] - bbox[1])


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    y: int,
    lines: Sequence[str],
    font: ImageFont.ImageFont,
    width: int,
    *,
    gap: int,
) -> int:
    current_y = y
    for index, line in enumerate(lines):
        current_y = _draw_centered_text(draw, current_y, line, font, width)
        if index < len(lines) - 1:
            current_y += gap
    return current_y


def _draw_card_border(draw: ImageDraw.ImageDraw, metrics: LayoutMetrics, top: int, bottom: int) -> None:
    draw.rounded_rectangle(
        (metrics.page_margin_x, top, metrics.receipt_width - metrics.page_margin_x, bottom),
        radius=metrics.card_radius,
        outline=0,
        width=2,
    )


def _measure_paragraph_block(
    draw: ImageDraw.ImageDraw,
    paragraphs: Sequence[str],
    font: ImageFont.ImageFont,
    max_width: int,
    metrics: LayoutMetrics,
) -> tuple[int, list[list[str]]]:
    wrapped_paragraphs: list[list[str]] = []
    total_height = 0
    line_height = _line_height(font, metrics.body_line_spacing)

    for index, paragraph in enumerate(paragraphs):
        lines = wrap_text(draw, paragraph, font, max_width)
        wrapped_paragraphs.append(lines)
        total_height += (len(lines) * line_height) - metrics.body_line_spacing
        if index < len(paragraphs) - 1:
            total_height += metrics.paragraph_gap

    return total_height, wrapped_paragraphs


def _measure_section_height(
    draw: ImageDraw.ImageDraw,
    section: ReceiptSection,
    section_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    metrics: LayoutMetrics,
) -> int:
    text_width = metrics.receipt_width - (2 * metrics.page_margin_x) - (2 * metrics.card_padding_x)
    body_height, _ = _measure_paragraph_block(draw, section.paragraphs, body_font, text_width, metrics)
    title_height = _line_height(section_font)
    return metrics.card_padding_y + title_height + metrics.section_title_gap + body_height + metrics.card_padding_y


def _draw_section_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    section: ReceiptSection,
    y: int,
    section_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    metrics: LayoutMetrics,
) -> int:
    del image
    content_x = metrics.page_margin_x + metrics.card_padding_x
    text_width = metrics.receipt_width - (2 * metrics.page_margin_x) - (2 * metrics.card_padding_x)
    title_y = y + metrics.card_padding_y
    draw.text((content_x, title_y), section.title, font=section_font, fill=0)

    body_height, wrapped_paragraphs = _measure_paragraph_block(draw, section.paragraphs, body_font, text_width, metrics)
    del body_height
    current_y = title_y + _line_height(section_font) + metrics.section_title_gap
    line_height = _line_height(body_font, metrics.body_line_spacing)

    for paragraph_index, lines in enumerate(wrapped_paragraphs):
        for line in lines:
            draw.text((content_x, current_y), line, font=body_font, fill=0)
            current_y += line_height
        current_y -= metrics.body_line_spacing
        if paragraph_index < len(wrapped_paragraphs) - 1:
            current_y += metrics.paragraph_gap

    bottom = current_y + metrics.card_padding_y
    _draw_card_border(draw, metrics, y, bottom)
    return bottom


def _choose_header_layout(
    draw: ImageDraw.ImageDraw,
    event: str,
    metrics: LayoutMetrics,
) -> tuple[ImageFont.ImageFont, list[str]]:
    max_width = metrics.receipt_width - (2 * metrics.page_margin_x)
    for size in (60, 56, 54, 52, 50, 48, 46, 44, 42, 40, 38, 36):
        scaled_size = max(18, int(round(size * (metrics.receipt_width / BASE_RECEIPT_WIDTH))))
        font = load_font("bold", scaled_size)
        lines = wrap_text(draw, event, font, max_width)
        if len(lines) <= 3:
            return font, lines

    fallback_font = load_font("bold", max(18, int(round(34 * (metrics.receipt_width / BASE_RECEIPT_WIDTH)))))
    return fallback_font, wrap_text(draw, event, fallback_font, max_width)


def _estimate_receipt_height(
    draw: ImageDraw.ImageDraw,
    event: str,
    sections: Sequence[ReceiptSection],
    footer_lines: Sequence[str],
    metrics: LayoutMetrics,
) -> tuple[int, ImageFont.ImageFont, list[str], ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont]:
    header_font, header_lines = _choose_header_layout(draw, event, metrics)
    section_font = load_font("bold", max(16, int(round(28 * (metrics.receipt_width / BASE_RECEIPT_WIDTH)))))
    body_font = load_font("regular", max(16, int(round(26 * (metrics.receipt_width / BASE_RECEIPT_WIDTH)))))
    footer_font = load_font("regular", max(14, int(round(20 * (metrics.receipt_width / BASE_RECEIPT_WIDTH)))))

    y = metrics.page_margin_y
    y += len(header_lines) * _line_height(header_font)
    if len(header_lines) > 1:
        y += (len(header_lines) - 1) * max(4, metrics.body_line_spacing - 1)
    y += metrics.header_gap_after
    y += 3
    y += metrics.divider_gap_after

    for index, section in enumerate(sections):
        y += _measure_section_height(draw, section, section_font, body_font, metrics)
        if index < len(sections) - 1:
            y += metrics.card_gap

    if footer_lines:
        if sections:
            y += metrics.card_gap
        y += 2
        y += metrics.footer_gap_after_divider
        for index, _line in enumerate(footer_lines):
            y += _line_height(footer_font)
            if index < len(footer_lines) - 1:
                y += metrics.footer_line_gap

    y += metrics.page_margin_y
    return y, header_font, header_lines, section_font, body_font, footer_font


def render_receipt_image(
    *,
    event: str,
    sections: Sequence[ReceiptSection],
    footer_lines: Sequence[str],
    receipt_width: int = BASE_RECEIPT_WIDTH,
) -> Image.Image:
    """Render one alert receipt as a thresholded monochrome image."""
    metrics = LayoutMetrics.for_width(receipt_width)
    probe = Image.new("L", (receipt_width, 10), color=255)
    probe_draw = ImageDraw.Draw(probe)
    (
        estimated_height,
        header_font,
        header_lines,
        section_font,
        body_font,
        footer_font,
    ) = _estimate_receipt_height(probe_draw, event, sections, footer_lines, metrics)

    image = Image.new("L", (receipt_width, estimated_height), color=255)
    draw = ImageDraw.Draw(image)

    y = metrics.page_margin_y
    y = _draw_centered_lines(
        draw,
        y,
        header_lines,
        header_font,
        receipt_width,
        gap=max(4, metrics.body_line_spacing - 1),
    )
    y += metrics.header_gap_after
    draw.line((metrics.page_margin_x, y, receipt_width - metrics.page_margin_x, y), fill=0, width=3)
    y += metrics.divider_gap_after

    for index, section in enumerate(sections):
        y = _draw_section_card(image, draw, section, y, section_font, body_font, metrics)
        if index < len(sections) - 1:
            y += metrics.card_gap

    if footer_lines:
        if sections:
            y += metrics.card_gap
        draw.line((metrics.page_margin_x, y, receipt_width - metrics.page_margin_x, y), fill=0, width=2)
        y += metrics.footer_gap_after_divider
        for index, line in enumerate(footer_lines):
            y = _draw_centered_text(draw, y, line, footer_font, receipt_width)
            if index < len(footer_lines) - 1:
                y += metrics.footer_line_gap

    y += metrics.page_margin_y
    cropped = image.crop((0, 0, receipt_width, min(y, estimated_height)))
    return prepare_receipt_image(cropped)
