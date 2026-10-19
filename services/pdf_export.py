"""Markdown → PDF rendering for tailored CVs and cover letters.

Pure-Python via ``fpdf2`` (optional extra). When the dep is missing we raise a
clear error rather than silently producing nothing — the UI gates the PDF
button so this only fires on misconfiguration.

The renderer supports a deliberately small subset of markdown — exactly what
the tailoring prompt produces:

  * `# / ## / ### / #### / #####` headings (sizes scaled)
  * `- ` and `* ` unordered lists
  * `**bold**` inline runs
  * Blank-line paragraph separation
  * Horizontal rules (``---``)

Anything else falls through as plain text, which is fine for ATS purposes —
recruiters parsing the PDF want plain text, not tables.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import List, Optional, Tuple

# Unicode TTF bundled with the repo (DejaVu: Latin incl. Polish/Czech/Turkish,
# Cyrillic, Greek, arrows, bullets, €). The built-in Helvetica is Latin-1 only,
# so without it "Łukasz" exports as "?ukasz" — a corrupted CV the user might
# send without noticing. PDF_FONT_DIR overrides the location.
_FONT_FAMILY = "DejaVu"
_FONT_FILES = {"": "DejaVuSans.ttf", "B": "DejaVuSans-Bold.ttf"}
_DEFAULT_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


class PDFExportError(RuntimeError):
    """Raised when PDF rendering can't proceed."""


# ---------------------------------------------------------------------------
# Markdown parsing — small block tokenizer
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,5})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_HR_RE = re.compile(r"^-{3,}\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _split_inline_bold(text: str) -> List[Tuple[str, bool]]:
    """Return ``[(segment, is_bold), …]`` so the renderer can switch fonts."""
    pieces: List[Tuple[str, bool]] = []
    last = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > last:
            pieces.append((text[last:m.start()], False))
        pieces.append((m.group(1), True))
        last = m.end()
    if last < len(text):
        pieces.append((text[last:], False))
    if not pieces:
        pieces.append((text, False))
    return pieces


def _ascii_safe(text: str) -> str:
    """Replace characters the built-in Helvetica font can't render.

    fpdf2's default core fonts are Latin-1. Em dashes, smart quotes, etc.
    crash the renderer, so the common offenders are mapped to plain ASCII
    first (nicer output than the fallback below). LLM-generated CV/cover-
    letter text is free-form, though, so any *other* out-of-Latin-1
    character (CJK, Cyrillic, uncommon emoji, ...) still needs to not crash
    the render -- those get replaced with "?" rather than raising.
    """
    mapped = (
        text.replace("—", "-")    # em dash
            .replace("–", "-")    # en dash
            .replace("‘", "'")
            .replace("’", "'")
            .replace("“", '"')
            .replace("”", '"')
            .replace("…", "...")  # ellipsis
            .replace("•", "-")    # bullet
            .replace(" ", " ")    # nbsp
            .replace(" ", " ")    # thin space
            .replace("​", "")     # zero-width space
            .replace("✅", "[OK]") # white-heavy-check-mark emoji
            .replace("⚠", "[!]")  # warning emoji
            .replace("✓", "[v]")  # check
            .replace("✗", "[x]")  # cross
    )
    return mapped.encode("latin-1", errors="replace").decode("latin-1")


def _font_dir() -> Optional[Path]:
    from utils.env import env_str

    d = Path(env_str("PDF_FONT_DIR") or _DEFAULT_FONT_DIR)
    return d if all((d / f).is_file() for f in _FONT_FILES.values()) else None


def _strip_invisible(text: str) -> str:
    return text.replace("\u200b", "").replace("\ufeff", "")


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

_HEADING_SIZES = {1: 18, 2: 14, 3: 12, 4: 11, 5: 11}


def markdown_to_pdf(text: str, title: str = "Document") -> bytes:
    """Render ``text`` as a single-column A4 PDF and return the bytes."""
    try:
        from fpdf import FPDF  # lazy import
    except ImportError as exc:
        raise PDFExportError(
            "PDF export requires the [pdf] extra. Install with "
            "`pip install -e \".[pdf]\"`."
        ) from exc

    try:
        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.set_margins(left=18, top=18, right=18)
        pdf.add_page()
        pdf.set_title(title)

        body_size = 11
        line_height = 6

        font_dir = _font_dir()
        if font_dir is not None:
            for style, filename in _FONT_FILES.items():
                pdf.add_font(_FONT_FAMILY, style, str(font_dir / filename))
            family, clean = _FONT_FAMILY, _strip_invisible
        else:  # no TTF available: Latin-1 core font with lossy fallback
            family, clean = "Helvetica", _ascii_safe

        def _set_body():
            pdf.set_font(family, size=body_size)

        _set_body()

        for raw_line in text.splitlines():
            line = clean(raw_line.rstrip())

            # IMPORTANT: in fpdf2 2.7+, ``multi_cell(0, …)`` leaves the cursor
            # at the right margin of the LAST rendered line — not back at
            # l_margin. The next ``multi_cell(0, …)`` then computes an
            # available width of 0 and raises "Not enough horizontal space".
            # Reset X every iteration.
            pdf.set_x(pdf.l_margin)

            if not line.strip():
                pdf.ln(line_height // 2)
                continue

            if _HR_RE.match(line):
                y = pdf.get_y() + 1
                pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
                pdf.ln(line_height // 2)
                continue

            m_head = _HEADING_RE.match(line)
            if m_head:
                level = len(m_head.group(1))
                content = m_head.group(2).strip()
                pdf.set_font(family, "B", _HEADING_SIZES.get(level, 11))
                pdf.ln(2)
                pdf.multi_cell(0, line_height + 1, content)
                _set_body()
                continue

            m_bul = _BULLET_RE.match(line)
            if m_bul:
                _render_bullet(pdf, m_bul.group(1), line_height)
                continue

            _render_inline(pdf, line, line_height)

        buf = io.BytesIO()
        pdf.output(buf)
        return buf.getvalue()
    except PDFExportError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize any fpdf2 failure
        raise PDFExportError(f"PDF rendering failed: {exc}") from exc


def _render_inline(pdf, text: str, line_height: int) -> None:
    """Render a paragraph line as plain text.

    Implementation note: an earlier version honored inline ``**bold**`` by
    mixing ``pdf.write()`` and ``pdf.multi_cell()`` calls, which left the
    cursor in a state that crashed fpdf2 with "Not enough horizontal space".
    Stripping the bold markers and rendering via a single ``multi_cell`` is
    reliable; the visual loss is minor and a tailored CV's emphasis comes
    primarily from headings, which still render bold.
    """
    plain = _BOLD_RE.sub(r"\1", text)
    if not plain:
        pdf.ln(line_height)
        return
    pdf.multi_cell(0, line_height, plain)


def _render_bullet(pdf, body: str, line_height: int) -> None:
    """Render a single bullet line.

    Implementation note: an earlier version split this into a fixed-width
    ``cell()`` + ``multi_cell(0, ...)``, which crashed inside fpdf2 with
    "Not enough horizontal space" because the cursor and the available-width
    computation didn't agree after the ``cell()`` call. Inlining the dash
    avoids the arithmetic altogether — visual output is identical.
    """
    _render_inline(pdf, "- " + body, line_height)
