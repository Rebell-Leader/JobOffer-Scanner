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
