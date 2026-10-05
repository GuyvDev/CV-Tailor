import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const cache = new Map();

// Execute the actual serverless TypeScript modules, without a deployed endpoint
// or a model call. External imports use the frontend's installed dependencies.
function loadTypeScript(url) {
  if (cache.has(url.href)) return cache.get(url.href).exports;
  const module = { exports: {} };
  cache.set(url.href, module);
  const code = ts.transpileModule(fs.readFileSync(url, "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const localRequire = (name) => name.startsWith(".")
    ? loadTypeScript(new URL(name.replace(/\.js$/, ".ts"), url))
    : require(name);
  new Function("require", "module", "exports", code)(localRequire, module, module.exports);
  return module.exports;
}

const { renderResume, compileResume, resumeLayoutVariants } = loadTypeScript(new URL("../api/stateless/resume.ts", import.meta.url));
const fixture = JSON.parse(fs.readFileSync(new URL("../../api/tests/fixtures/resume_layout.json", import.meta.url), "utf8"));

test("default output starts with comfortable spacing", () => {
  assert.ok(renderResume(fixture).includes('#let resume-density = "comfortable"'));
});

test("each default preset produces a real one-page PDF", () => {
  for (const density of ["compact", "comfortable", "spacious"]) {
    const source = renderResume(fixture, density);
    const result = compileResume(source);
    assert.equal(result.pageCount, 1);
    assert.equal(result.source, source);
    assert.equal(result.logs.length, 1);
    assert.ok(result.pdf.subarray(0, 5).equals(Buffer.from("%PDF-")));
  }
});

test("dense content fits by tightening spacing while preserving all text", () => {
  const draft = structuredClone(fixture);
  draft.profile += " Investigated timing-sensitive failures and verified the behavior through repeatable testing.".repeat(12);
  const source = renderResume(draft, "spacious");
  const result = compileResume(source);
  assert.equal(result.pageCount, 1);
  assert.ok(result.logs.length > 1);
  assert.equal(result.source.replace(/^#let resume-density = ".+"$/m, ""), source.replace(/^#let resume-density = ".+"$/m, ""));
  assert.equal(compileResume(source).source, result.source);
});

test("oversized bullets paginate and page_count reflects the real PDF", () => {
  const draft = structuredClone(fixture);
  draft.projects[0].bullets = ["Verified technical evidence and repeatable debugging workflow. ".repeat(200) + "Final evidence marker."];
  const result = compileResume(renderResume(draft, "compact"));
  assert.ok(result.pageCount > 1);
  assert.equal(result.logs.length, 1);
});

test("empty sections and projects do not leave orphan headings", () => {
  const draft = structuredClone(fixture);
  draft.profile = "";
  draft.education = {};
  draft.projects[0].bullets = [" "];
  draft.skills = [];
  const source = renderResume(draft);
  for (const title of ["Profile", "Education", "Skills"]) assert.ok(!source.includes(`#section("${title}")`));
  assert.ok(!source.includes(draft.projects[0].title));
  assert.ok(source.includes("#project(first: true)[Operating Systems Laboratory"));
  assert.equal(compileResume(source).pageCount, 1);
});

test("template substitutions preserve literal dollar signs in CV content", () => {
  const draft = structuredClone(fixture);
  draft.contact.name = "Example $& Candidate";
  draft.profile = "Maintained the $display debugging workflow and verified results.";
  const source = renderResume(draft);
  assert.ok(source.includes("Example \\$& Candidate"));
  assert.ok(source.includes("\\$display debugging workflow"));
  assert.ok(!source.includes("{{"));
  assert.equal(compileResume(source).pageCount, 1);
});

test("unknown densities safely use compact and presets never alter font or margins", () => {
  const source = renderResume(fixture, 'unknown"');
  assert.ok(source.includes('#let resume-density = "compact"'));
  assert.equal([...resumeLayoutVariants(source)].length, 1);
  const variants = [...resumeLayoutVariants(renderResume(fixture, "spacious"))];
  assert.equal(variants.length, 3);
  const normalize = (value) => value.replace(/^#let resume-density = ".+"$/m, "");
  assert.ok(variants.every((value) => normalize(value) === normalize(variants[0])));
});

test("syntax errors stop immediately and evict the compiler cache", () => {
  let calls = 0, evictions = 0;
  const compiler = {
    compile() { calls++; return { hasError: () => true, result: null }; },
    pdf() { throw new Error("PDF export should not run"); },
    evictCache() { evictions++; },
  };
  assert.throws(() => compileResume(renderResume(fixture, "spacious"), compiler), /Typst compilation failed/);
  assert.equal(calls, 1);
  assert.equal(evictions, 1);
});

test("custom layouts are compiled once without rewriting", () => {
  const source = '#set page(height: 200pt)\nFirst page.\n#pagebreak()\nSecond page.';
  const result = compileResume(source);
  assert.equal(result.source, source);
  assert.equal(result.logs.length, 1);
  assert.equal(result.pageCount, 2);
});
