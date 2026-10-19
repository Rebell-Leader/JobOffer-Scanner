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

// Turn a non-2xx /v1/analyze response into an actionable popup message.
// `detail` is the FastAPI error body's `detail` field (or null/undefined
// when the body wasn't JSON) — the REST API already maps quota/rate-limit
// errors to 402/429 with a human-readable detail string, so surfacing it
// beats a bare status code.
function describeApiError(status, detail) {
  const suffix = detail ? ` ${detail}` : "";
  switch (status) {
    case 401:
      return "Token rejected (401). Check your API token.";
    case 402:
      return `Plan limit reached (402).${suffix || " Upgrade your plan to keep analyzing."}`;
    case 403:
      return `Access denied (403).${suffix}`;
    case 404:
      return `Not found (404).${suffix}`;
    case 429:
      return `Rate limited (429).${suffix || " Try again shortly."}`;
    default:
      if (status >= 500) {
        return `Server error (${status}). Try again shortly.`;
      }
      return `API error ${status}.${suffix}`;
  }
}

// Export for Node tests; harmless in the browser (module is undefined there,
// guarded so the popup can load this file directly via <script>).
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    extractJobText,
    normalizeWhitespace,
    buildAnalyzeRequest,
    renderVerdict,
    describeApiError,
  };
}
