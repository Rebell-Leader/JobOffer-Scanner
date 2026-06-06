// Popup controller. Relies on extract.js (loaded first) for the pure helpers.
/* global chrome, extractJobText, buildAnalyzeRequest, renderVerdict */

const $ = (id) => document.getElementById(id);

// --- Settings persistence (chrome.storage.local) ---------------------------

async function loadSettings() {
  const { baseUrl = "", token = "" } = await chrome.storage.local.get([
    "baseUrl",
    "token",
  ]);
  $("baseUrl").value = baseUrl;
  $("token").value = token;
  // Auto-open settings if unconfigured.
  if (!baseUrl || !token) $("settings").open = true;
}

async function saveSettings() {
  await chrome.storage.local.set({
    baseUrl: $("baseUrl").value.trim(),
    token: $("token").value.trim(),
  });
  $("settings").open = false;
  $("error").textContent = "";
  $("verdict").textContent = "Settings saved.";
}

// --- Page extraction (runs extractJobText in the active tab) ----------------

async function grabPageText() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    // The injected function can't see extract.js, so inline the extraction.
    // It returns innerText of the best-matching container or the body.
    func: () => {
      const sels = [
        '[class*="job-description" i]',
        '[class*="jobDescription" i]',
        '[class*="description" i]',
        "article",
        "main",
      ];
      for (const s of sels) {
        let el = null;
        try {
          el = document.querySelector(s);
        } catch (e) {
          el = null;
        }
        if (el && el.innerText && el.innerText.trim().length >= 200) {
          return el.innerText;
        }
      }
      return document.body ? document.body.innerText : "";
    },
  });
  return (result || "").trim();
}

// --- Analyze ----------------------------------------------------------------

async function analyze() {
  $("error").textContent = "";
  $("verdict").textContent = "";
  $("report").textContent = "";

  const { baseUrl = "", token = "" } = await chrome.storage.local.get([
    "baseUrl",
    "token",
  ]);
  if (!baseUrl || !token) {
    $("error").textContent = "Set the API base URL and token first.";
    $("settings").open = true;
    return;
  }

  let jobText = "";
  try {
    jobText = await grabPageText();
  } catch (e) {
    $("error").textContent = "Couldn't read this page: " + e.message;
    return;
  }
  if (jobText.length < 100) {
    $("error").textContent =
      "This page didn't yield enough text. Open the full job posting first.";
    return;
  }

  $("verdict").textContent = "Analyzing…";
  const { url, options } = buildAnalyzeRequest(baseUrl, token, jobText);
  try {
    const resp = await fetch(url, options);
    if (resp.status === 401) {
      $("verdict").textContent = "";
      $("error").textContent = "Token rejected (401). Check your API token.";
      return;
    }
    if (!resp.ok) {
      $("verdict").textContent = "";
      $("error").textContent = `API error ${resp.status}.`;
      return;
    }
    const data = await resp.json();
    $("verdict").textContent = renderVerdict(data);
    $("report").textContent = data.final_report || "(no report returned)";
  } catch (e) {
    $("verdict").textContent = "";
    $("error").textContent = "Request failed: " + e.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  $("save").addEventListener("click", saveSettings);
  $("analyze").addEventListener("click", analyze);
});
