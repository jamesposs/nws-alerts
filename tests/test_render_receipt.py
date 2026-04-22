from __future__ import annotations

import unittest
from unittest.mock import patch

from app import render_receipt as render_receipt_module
from app.render_receipt import ReceiptSection, render_receipt_image


def sample_sections() -> tuple[ReceiptSection, ...]:
    return (
        ReceiptSection("STARTS", ("2:15 PM",)),
        ReceiptSection("EXPIRES", ("3:00 PM",)),
        ReceiptSection(
            "DESCRIPTION",
            (
                "At 2:11 PM, a severe thunderstorm was located over Fairfax, moving east at 35 mph.",
                "Expect damaging wind gusts and quarter-size hail.",
            ),
        ),
        ReceiptSection(
            "INSTRUCTION",
            ("Move to an interior room on the lowest floor of a sturdy building. Stay away from windows.",),
        ),
    )


class ReceiptRenderTests(unittest.TestCase):
    def test_render_receipt_returns_print_ready_image(self) -> None:
        image = render_receipt_image(
            event="Severe Thunderstorm Warning",
            sections=sample_sections(),
            footer_lines=("SEVERE | IMMEDIATE | OBSERVED", "ACTUAL | ALERT", "Sent 2:11 PM"),
            receipt_width=576,
        )

        self.assertEqual(image.mode, "1")
        self.assertEqual(image.width, 576)
        self.assertGreater(image.height, 300)

    def test_render_receipt_keeps_morning_brief_style_order(self) -> None:
        original_text = render_receipt_module.ImageDraw.ImageDraw.text
        captured_texts: list[str] = []

        def capturing_text(draw, xy, text, *args, **kwargs):
            captured_texts.append(str(text))
            return original_text(draw, xy, text, *args, **kwargs)

        with patch("PIL.ImageDraw.ImageDraw.text", new=capturing_text):
            render_receipt_image(
                event="Severe Thunderstorm Warning",
                sections=sample_sections(),
                footer_lines=("SEVERE | IMMEDIATE | OBSERVED", "ACTUAL | ALERT", "Sent 2:11 PM"),
                receipt_width=576,
            )

        header_index = next(
            index for index, text in enumerate(captured_texts) if text in {"Severe", "Thunderstorm", "Warning"}
        )
        self.assertLess(header_index, captured_texts.index("STARTS"))
        self.assertLess(captured_texts.index("STARTS"), captured_texts.index("EXPIRES"))
        self.assertLess(captured_texts.index("EXPIRES"), captured_texts.index("DESCRIPTION"))
        self.assertLess(captured_texts.index("DESCRIPTION"), captured_texts.index("INSTRUCTION"))
        self.assertIn("SEVERE | IMMEDIATE | OBSERVED", captured_texts)
        self.assertIn("Sent 2:11 PM", captured_texts)


if __name__ == "__main__":
    unittest.main()
