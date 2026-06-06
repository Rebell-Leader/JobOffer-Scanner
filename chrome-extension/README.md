# JobOffer Scanner — Chrome extension

One-click analysis of the job posting on the current tab, using your
JobOffer Scanner account via the REST API.

## Why an extension (vs. the URL-ingest feature)

JS-heavy boards (LinkedIn / Indeed / Glassdoor) render postings client-side,
so the server's `requests`-based URL ingest often gets an empty shell. The
extension reads the **already-rendered** page in your browser, so those
boards work without a headless browser on the server.

## Install (developer mode)

1. Run the REST API somewhere reachable (`python -m api.main`, or behind your
   reverse proxy at `https://yourdomain.com`).
2. In the web app, create an API token: sidebar → **🔑 API tokens** → Create.
   Copy the `jos_…` value (shown once).
3. In Chrome: `chrome://extensions` → enable **Developer mode** → **Load
   unpacked** → select this `chrome-extension/` folder.
4. Click the extension icon → **Settings** → paste your API base URL
   (e.g. `https://yourdomain.com`) and the token → **Save settings**.

## Use

Open a job posting, click the extension icon, **Analyze this page**. The
popup shows the Green/Yellow/Red verdict and the full report. Nothing is
saved server-side (`save: false`); use the web app to save + track.

## Files

- `manifest.json` — MV3 manifest (activeTab + scripting + storage).
- `popup.html` / `popup.js` — UI + controller.
- `extract.js` — pure helpers (text extraction, request building, verdict
  rendering), unit-tested in `chrome-extension/extract.test.mjs`.

## Test the pure helpers

```
node chrome-extension/extract.test.mjs
```
