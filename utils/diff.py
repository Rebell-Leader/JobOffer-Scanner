"""Plain-text unified diff used by the master-CV history and artifact compare.

Thin wrapper around ``difflib`` that:
  * Splits on lines without losing the final newline.
  * Suppresses the noisy ``--- file +++ file`` header when both labels are
    given as empty (callers that want their own header).
  * Produces a single string ready to drop into ``st.code(diff, language="diff")``
    so Streamlit's built-in syntax highlighting renders +/- coloured lines.
"""

from __future__ import annotations

import difflib
import re
from typing import List


def unified_diff(
    before: str,
    after: str,
    before_label: str = "before",
    after_label: str = "after",
    context: int = 3,
) -> str:
    """Return a unified diff of two strings.

    Empty result means the two strings are identical (after the normal newline
    handling) — the caller can rely on truthiness to render "No changes".
    """
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    chunks = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=before_label,
        tofile=after_label,
        n=context,
        lineterm="",
    )
    return "".join(chunks)


def has_differences(before: str, after: str) -> bool:
    return (before or "").strip() != (after or "").strip()


# ---------------------------------------------------------------------------
# Inline (word-level) diff — for in-place highlighted rendering
# ---------------------------------------------------------------------------

# Background colors chosen to read on both light and dark Streamlit themes.
_INS_STYLE = (
    "background-color: rgba(40, 180, 99, 0.25); "
    "color: inherit; padding: 0 2px; border-radius: 2px;"
)
_DEL_STYLE = (
    "background-color: rgba(231, 76, 60, 0.25); "
    "color: inherit; padding: 0 2px; border-radius: 2px; "
    "text-decoration: line-through;"
)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def inline_diff_html(before: str, after: str) -> str:
    """Word-level diff rendered as HTML for in-place display.

    Returns one HTML fragment (no surrounding ``<html>`` tags) where:

      * Unchanged words appear inline as plain text.
      * Added words are wrapped in a green ``<ins>``-style span.
      * Removed words are wrapped in a red, strikethrough ``<del>``-style span.

    Newlines in the input are preserved as ``<br>`` tags so the rendered
    diff visually matches the original layout. HTML metacharacters in the
    input are escaped before being inserted into the output.

    Intended for ``st.markdown(inline_diff_html(a, b), unsafe_allow_html=True)``.
    """
    before = before or ""
    after = after or ""

    # Tokenise on word/whitespace boundaries — splitting on whole words drops
    # the inter-word whitespace, which makes the rendered diff hard to read.
    # The pattern captures runs of \S as one token and runs of whitespace
    # (including newlines) as their own tokens, so we can rebuild the layout.
    token_re = re.compile(r"\S+|\s+")
    a_tokens = token_re.findall(before)
    b_tokens = token_re.findall(after)

    matcher = difflib.SequenceMatcher(a=a_tokens, b=b_tokens, autojunk=False)
    out: List[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out.append(_render_run(a_tokens[i1:i2], None))
        elif op == "delete":
            out.append(_render_run(a_tokens[i1:i2], _DEL_STYLE))
        elif op == "insert":
            out.append(_render_run(b_tokens[j1:j2], _INS_STYLE))
        elif op == "replace":
            out.append(_render_run(a_tokens[i1:i2], _DEL_STYLE))
            out.append(_render_run(b_tokens[j1:j2], _INS_STYLE))
    return "".join(out)


def _render_run(tokens: List[str], style: str | None) -> str:
    """Render a run of tokens, optionally wrapped in a styled span.

    Whitespace tokens (including ``\\n``) are emitted between styled spans
    rather than inside them, so:
      - Blank-line spacing survives the diff,
      - The colored highlight only covers actual words (not the preceding /
        following whitespace), which reads cleaner.
    """
    if not tokens:
        return ""
    parts: List[str] = []
    buffer: List[str] = []  # accumulating non-whitespace tokens
    for tok in tokens:
        if tok.isspace():
            if buffer:
                parts.append(_wrap(" ".join(buffer), style))
                buffer.clear()
            parts.append(_html_escape(tok).replace("\n", "<br>"))
        else:
            buffer.append(tok)
    if buffer:
        parts.append(_wrap(" ".join(buffer), style))
    return "".join(parts)


def _wrap(text: str, style: str | None) -> str:
    escaped = _html_escape(text)
    if style is None:
        return escaped
    return f'<span style="{style}">{escaped}</span>'
