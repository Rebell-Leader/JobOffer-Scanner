"""Shared HTML → plain-text extraction for job postings.

Both the plain-fetch path (``tools.url_ingest``) and the headless-browser path
(``tools.browser_scraper``) need to turn posting HTML into clean text the same
way: drop chrome (script/style/nav/…), prefer a job-description-shaped
container, then collapse whitespace. This is that single implementation.
"""

from __future__ import annotations

import re
from typing import Union

from bs4 import BeautifulSoup

# Chrome elements that never carry posting content.
_STRIP_TAGS = ("script", "style", "noscript", "header", "footer", "nav", "svg")
# Container classes that usually wrap the posting body.
_CONTENT_CLASS_RE = re.compile(r"(job|posting|description|content|details)", re.I)


def extract_job_text(html: Union[str, bytes]) -> str:
    """Return cleaned posting text from raw HTML (``str`` or ``bytes``)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    candidates = soup.find_all(
        ["article", "main", "section", "div"],
        attrs={"class": _CONTENT_CLASS_RE},
    )
    root = candidates[0] if candidates else soup
    text = root.get_text(separator="\n", strip=True)
    # Collapse runs of blank lines.
    return re.sub(r"\n{3,}", "\n\n", text)
