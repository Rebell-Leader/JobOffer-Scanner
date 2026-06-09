"""Small text-processing helpers shared across LLM-output handling.

Leaf utilities (only ``re``); safe to import from tools/ and services/.
"""

from __future__ import annotations

import re

_FENCE_OPEN = re.compile(r"^```[a-zA-Z0-9_-]*\s*")
_FENCE_CLOSE = re.compile(r"\s*```$")


def strip_code_fence(text: str) -> str:
    """Strip a leading ```` ```json ```` / ```` ``` ```` fence and trailing ```` ``` ````.

    Returns the inner content stripped of surrounding whitespace. A string with
    no fence is returned trimmed. This is the single implementation used by the
    job/bulk-import/company-research parsers (which previously each rolled their
    own).
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = _FENCE_OPEN.sub("", s)
        s = _FENCE_CLOSE.sub("", s)
    return s.strip()
