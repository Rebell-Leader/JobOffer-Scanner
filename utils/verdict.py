"""Structured Green / Yellow / Red verdict.

The LLM emits a JSON sidecar (verdict + top reasons + confidence) tagged with
``<verdict_json>...</verdict_json>``. We extract it deterministically; if the
model omits or malforms it, we infer a verdict from textual cues so the UI
always has something to render.
"""

from __future__ import annotations

import json
import re
from typing import Dict

VERDICT_LIGHTS = {
    "Highly Recommended": "green",
    "Recommended": "green",
    "Consider with Caution": "yellow",
    "Not Recommended": "red",
}

_VERDICT_BLOCK_RE = re.compile(
    r"<verdict_json>(.*?)</verdict_json>", re.DOTALL | re.IGNORECASE
)


def extract_verdict(report_markdown: str) -> Dict[str, object]:
    """Return ``{verdict, light, reasons, confidence, source}``.

    ``source`` is ``"structured"`` when we parsed the JSON sidecar and
    ``"inferred"`` when we fell back to scanning the markdown for known
    verdict phrases.
    """
    match = _VERDICT_BLOCK_RE.search(report_markdown or "")
    if match:
        try:
            data = json.loads(match.group(1).strip())
            verdict = str(data.get("verdict", "")).strip()
            light = VERDICT_LIGHTS.get(verdict, "yellow")
            reasons = [str(r) for r in (data.get("reasons") or [])][:5]
            confidence = data.get("confidence")
            return {
                "verdict": verdict or "Consider with Caution",
                "light": light,
                "reasons": reasons,
                "confidence": confidence,
                "source": "structured",
            }
        except (json.JSONDecodeError, TypeError):
            pass

    # Inference fallback: look for any known verdict phrase in the text.
    text = report_markdown or ""
    # Check most-specific labels first so "Recommended" doesn't pre-empt
    # "Highly Recommended".
    for phrase in (
        "Highly Recommended",
        "Not Recommended",
        "Consider with Caution",
        "Recommended",
    ):
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE):
            return {
                "verdict": phrase,
                "light": VERDICT_LIGHTS[phrase],
                "reasons": [],
                "confidence": None,
                "source": "inferred",
            }

    return {
        "verdict": "Consider with Caution",
        "light": "yellow",
        "reasons": [],
        "confidence": None,
        "source": "inferred",
    }


def strip_verdict_block(report_markdown: str) -> str:
    """Remove the verdict JSON sidecar from the markdown shown to the user."""
    return _VERDICT_BLOCK_RE.sub("", report_markdown or "").strip()
