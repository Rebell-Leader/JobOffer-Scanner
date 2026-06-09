"""Unit tests for PDF export (Phase 12 — part 1)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# Skip the entire suite gracefully when fpdf2 isn't available so CI on
# minimal environments still reports green for the rest of the project.
try:
    import fpdf  # noqa: F401  (presence check)
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False


@unittest.skipUnless(HAS_FPDF, "fpdf2 not installed; PDF export unavailable")
class PDFExportTests(unittest.TestCase):
    def test_returns_bytes_starting_with_pdf_header(self):
        from services.pdf_export import markdown_to_pdf

        sample = (
            "# Jane Doe\n\n"
            "## Summary\n\n"
            "ML engineer with **Python** and PyTorch experience.\n\n"
            "## Skills\n\n"
            "Python, PyTorch, AWS, Docker\n\n"
            "## Experience\n\n"
            "### Senior ML Engineer — Acme\n\n"
            "- Built recsys serving 10M users\n"
            "- Cut p99 latency by 40%\n"
        )
        out = markdown_to_pdf(sample, title="Jane Doe — Tailored CV")
        self.assertIsInstance(out, (bytes, bytearray))
        # Every valid PDF starts with the magic header.
        self.assertTrue(bytes(out[:5]) == b"%PDF-")
        # Sanity: should be more than a stub.
        self.assertGreater(len(out), 1000)

    def test_handles_unicode_em_dash_and_bullets(self):
        """Em dashes / smart quotes / unicode bullets must not crash the renderer."""
        from services.pdf_export import markdown_to_pdf

        sample = (
            "# Title — with em dash\n\n"
            "Plain paragraph with “smart quotes” and an ellipsis…\n\n"
            "• Pre-bulleted line that doesn't use markdown dash\n"
        )
        out = markdown_to_pdf(sample)
        self.assertTrue(bytes(out[:5]) == b"%PDF-")

    def test_empty_input_renders_blank_pdf(self):
        from services.pdf_export import markdown_to_pdf

        out = markdown_to_pdf("")
        self.assertTrue(bytes(out[:5]) == b"%PDF-")

    def test_heading_and_inline_bold_parsing(self):
        from services.pdf_export import _HEADING_RE, _split_inline_bold

        m = _HEADING_RE.match("### Senior ML Engineer — Acme")
        self.assertIsNotNone(m)
        self.assertEqual(len(m.group(1)), 3)
        self.assertIn("Senior ML Engineer", m.group(2))

        segs = _split_inline_bold("Use **Python** and **PyTorch** here.")
        # Five segments: "Use ", "Python", " and ", "PyTorch", " here."
        bolds = [s for s, b in segs if b]
        plains = [s for s, b in segs if not b]
        self.assertEqual(bolds, ["Python", "PyTorch"])
        self.assertEqual(plains, ["Use ", " and ", " here."])

    def test_ascii_safe_replacements(self):
        from services.pdf_export import _ascii_safe

        self.assertEqual(_ascii_safe("a — b"), "a - b")
        self.assertEqual(_ascii_safe("“hi”"), '"hi"')
        self.assertEqual(_ascii_safe("dots…"), "dots...")


class PDFExportMissingDepTests(unittest.TestCase):
    def test_raises_when_fpdf_missing(self):
        """When fpdf2 isn't installed, markdown_to_pdf must raise a clear
        PDFExportError instead of silently returning bytes-like junk."""
        from unittest import mock

        import services.pdf_export as mod

        # Force the import inside markdown_to_pdf to fail.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fpdf":
                raise ImportError("simulated absence")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(mod.PDFExportError):
                mod.markdown_to_pdf("# x")


if __name__ == "__main__":
    unittest.main()
