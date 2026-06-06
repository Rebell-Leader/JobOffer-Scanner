// Pure, testable helpers shared by the popup + content extraction.
// No DOM/chrome globals at module scope so this can be unit-tested in Node.

// Heuristic: prefer a job-description-ish container, else fall back to the
// whole body text. Mirrors the server-side tools/url_ingest._clean_html
// intent but runs in the live (already-rendered) page, so JS-heavy boards
// that the server can't fetch work here.
function extractJobText(doc) {
  if (!doc || !doc.body) return "";
  const selectors = [
    '[class*="job-description" i]',
    '[class*="jobDescription" i]',
    '[class*="description" i]',
    '[id*="job-description" i]',
    "article",
    "main",
  ];
  for (const sel of selectors) {
    let el = null;
    try {
      el = doc.querySelector(sel);
    } catch (e) {
      el = null;
    }
    if (el && el.innerText && el.innerText.trim().length >= 200) {
      return normalizeWhitespace(el.innerText);
    }
  }
  return normalizeWhitespace(doc.body.innerText || "");
}

function normalizeWhitespace(text) {
  return (text || "")
    .replace(/\r/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// Build the /v1/analyze request from settings + extracted text.
function buildAnalyzeRequest(baseUrl, token, jobText) {
  const trimmed = (baseUrl || "").replace(/\/+$/, "");
  return {
    url: `${trimmed}/v1/analyze`,
    options: {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ job_posting: jobText, save: false }),
    },
  };
}

// Compact verdict line for the popup.
function renderVerdict(result) {
  const v = (result && result.verdict) || {};
  const light = v.light || "yellow";
  const emoji = { green: "🟢", yellow: "🟡", red: "🔴" }[light] || "⚪";
  const label = v.verdict || "Consider with Caution";
  return `${emoji} ${label}`;
}

// Export for Node tests; harmless in the browser (module is undefined there,
// guarded so the popup can load this file directly via <script>).
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    extractJobText,
    normalizeWhitespace,
    buildAnalyzeRequest,
    renderVerdict,
  };
}
