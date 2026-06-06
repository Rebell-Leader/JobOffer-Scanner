// Node test for the pure helpers in extract.js. Run: node extract.test.mjs
import assert from "node:assert";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  extractJobText,
  normalizeWhitespace,
  buildAnalyzeRequest,
  renderVerdict,
} = require("./extract.js");

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`  ok - ${name}`);
}

// --- normalizeWhitespace ---
test("normalizeWhitespace collapses blank lines + trims", () => {
  assert.equal(normalizeWhitespace("a\r\n\n\n\nb  \n"), "a\n\nb");
});

// --- extractJobText ---
test("extractJobText prefers a job-description container", () => {
  const longText = "Job description ".repeat(40); // > 200 chars
  const doc = {
    body: { innerText: "nav junk" },
    querySelector: (sel) =>
      sel.includes("description")
        ? { innerText: longText }
        : null,
  };
  const out = extractJobText(doc);
  assert.ok(out.includes("Job description"));
  assert.ok(!out.includes("nav junk"));
});

test("extractJobText falls back to body when no container matches", () => {
  const body = "Body level posting text ".repeat(20);
  const doc = { body: { innerText: body }, querySelector: () => null };
  assert.ok(extractJobText(doc).includes("Body level posting text"));
});

test("extractJobText ignores too-short containers", () => {
  const body = "The real long body text here ".repeat(20);
  const doc = {
    body: { innerText: body },
    querySelector: (sel) =>
      sel.includes("description") ? { innerText: "tiny" } : null,
  };
  // The short .description is skipped; body is used.
  assert.ok(extractJobText(doc).includes("real long body text"));
});

test("extractJobText handles a broken querySelector", () => {
  const doc = {
    body: { innerText: "fallback body content ".repeat(20) },
    querySelector: () => {
      throw new Error("invalid selector");
    },
  };
  assert.ok(extractJobText(doc).includes("fallback body content"));
});

test("extractJobText returns empty for a doc without a body", () => {
  assert.equal(extractJobText({}), "");
  assert.equal(extractJobText(null), "");
});

// --- buildAnalyzeRequest ---
test("buildAnalyzeRequest strips trailing slashes + sets bearer", () => {
  const { url, options } = buildAnalyzeRequest(
    "https://x.com///",
    "jos_abc",
    "posting text",
  );
  assert.equal(url, "https://x.com/v1/analyze");
  assert.equal(options.method, "POST");
  assert.equal(options.headers.Authorization, "Bearer jos_abc");
  const body = JSON.parse(options.body);
  assert.equal(body.job_posting, "posting text");
  assert.equal(body.save, false);
});

// --- renderVerdict ---
test("renderVerdict maps light to emoji", () => {
  assert.ok(renderVerdict({ verdict: { verdict: "Recommended", light: "green" } }).includes("🟢"));
  assert.ok(renderVerdict({ verdict: { verdict: "Not Recommended", light: "red" } }).includes("🔴"));
});

test("renderVerdict has a sane default for missing verdict", () => {
  const out = renderVerdict({});
  assert.ok(out.includes("Consider with Caution"));
});

console.log(`\n${passed} extract.js tests passed`);
