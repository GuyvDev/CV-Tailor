import { NodeCompiler } from "@myriaddreamin/typst-ts-node-compiler";

type Project = {
  title: string;
  dates?: string;
  stack?: string[];
  cv_status?: string;
  facts?: string[];
};

type Draft = {
  contact: {
    name: string;
    email?: string;
    location?: string;
    linkedin?: string;
    github?: string;
  };
  headline?: string;
  profile: string;
  education: {
    school?: string;
    degree?: string;
    dates?: string;
    bullets?: string[];
  };
  projects: Array<{
    title: string;
    stack: string[];
    dates?: string;
    bullets: string[];
  }>;
  skills: Array<{
    category: string;
    items: string[];
  }>;
  fit_summary?: string;
  keywords?: string[];
  job_title?: string;
};

type ScoreReport = {
  quality_score: number;
  match_score: number;
  score_band: string;
  decision: string;
  summary: string;
  strengths: string[];
  gaps: string[];
  recommendations: string[];
  revision_brief: string;
};

const MAX_PAYLOAD_BYTES = 128_000;
const MAX_OUTPUT_BYTES = 4_300_000;
const MAX_GENERATION_OUTPUT_TOKENS = 5_000;
const MAX_SCORE_OUTPUT_TOKENS = 1_000;
const MAX_CANDIDATE_CHARS = 16_000;
const MAX_JOB_DESCRIPTION_CHARS = 16_000;
const MAX_PROJECTS_CHARS = 12_000;
const MAX_SKILLS_CHARS = 8_000;
const MAX_RULES_CHARS = 4_000;
const IP_REQUEST_LIMIT = 5;
const IP_REQUEST_WINDOW_SECONDS = 60 * 60;
const DAILY_REQUEST_LIMIT = 100;
const inMemoryCounters = new Map<string, { count: number; expiresAt: number }>();

type RateLimitDecision = { allowed: true } | { allowed: false; detail: string };

function jsonResponse(res: any, status: number, payload: unknown) {
  res.status(status).setHeader("Content-Type", "application/json");
  res.send(JSON.stringify(payload));
}

function clientIpFingerprint(req: any) {
  const forwarded = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  const value = forwarded || String(req.headers["x-real-ip"] || "unknown");
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function secondsUntilNextUtcDay() {
  const now = new Date();
  const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return Math.max(1, Math.ceil((next - now.getTime()) / 1000));
}

function incrementInMemoryCounter(key: string, ttlSeconds: number) {
  const now = Date.now();
  const current = inMemoryCounters.get(key);
  if (!current || current.expiresAt <= now) {
    inMemoryCounters.set(key, { count: 1, expiresAt: now + ttlSeconds * 1000 });
    return 1;
  }
  current.count += 1;
  return current.count;
}

async function consumeRateLimits(req: any): Promise<RateLimitDecision> {
  const url = process.env.UPSTASH_REDIS_REST_URL?.replace(/\/$/, "");
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  const ipKey = `cv-tailor:stateless:ip:${clientIpFingerprint(req)}`;
  const dayKey = `cv-tailor:stateless:day:${new Date().toISOString().slice(0, 10)}`;
  const dailyTtlSeconds = secondsUntilNextUtcDay();

  if (!url || !token) {
    // Local development and an initial Vercel deployment can run without Upstash.
    // These counters are per warm serverless instance, so they are not a durable global limit.
    const ipCount = incrementInMemoryCounter(ipKey, IP_REQUEST_WINDOW_SECONDS);
    if (ipCount > IP_REQUEST_LIMIT) return { allowed: false, detail: "Rate limit reached: up to 5 CV generations per IP address each hour." };
    const dailyCount = incrementInMemoryCounter(dayKey, dailyTtlSeconds);
    if (dailyCount > DAILY_REQUEST_LIMIT) return { allowed: false, detail: "Daily site limit reached: up to 100 CV generations per day. Please try again tomorrow." };
    return { allowed: true };
  }

  try {
    const increment = async (key: string, ttlSeconds: number) => {
      const response = await fetch(`${url}/pipeline`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify([["INCR", key], ["EXPIRE", key, ttlSeconds, "NX"]]),
      });
      if (!response.ok) throw new Error("counter store unavailable");
      const results = await response.json();
      const count = Number(results?.[0]?.result);
      if (!Number.isFinite(count)) throw new Error("invalid counter response");
      return count;
    };
    const ipCount = await increment(ipKey, IP_REQUEST_WINDOW_SECONDS);
    if (ipCount > IP_REQUEST_LIMIT) return { allowed: false, detail: "Rate limit reached: up to 5 CV generations per IP address each hour." };
    const dailyCount = await increment(dayKey, dailyTtlSeconds);
    if (dailyCount > DAILY_REQUEST_LIMIT) return { allowed: false, detail: "Daily site limit reached: up to 100 CV generations per day. Please try again tomorrow." };
    return { allowed: true };
  } catch {
    return { allowed: false, detail: "Public generation is temporarily unavailable because the rate-limit service could not be reached." };
  }
}

function validateInputSize(body: any): string | null {
  const limits: Array<[string, unknown, number]> = [
    ["Candidate profile", body?.candidate_profile, MAX_CANDIDATE_CHARS],
    ["Job description", body?.job_description, MAX_JOB_DESCRIPTION_CHARS],
    ["Projects JSON", body?.projects_json, MAX_PROJECTS_CHARS],
    ["Skills JSON", body?.skills_json, MAX_SKILLS_CHARS],
    ["Generation rules", body?.rules, MAX_RULES_CHARS],
  ];
  for (const [label, value, maxLength] of limits) {
    if (typeof value === "string" && value.length > maxLength) return `${label} is too long (maximum ${maxLength.toLocaleString()} characters).`;
  }
  return null;
}

function safePart(value: string, maxLen = 80) {
  return (value || "tailored-resume").trim().replace(/[^\w-]/g, "_").slice(0, maxLen).replace(/^_+|_+$/g, "") || "tailored-resume";
}

function escapeTypst(value: string) {
  return String(value || "")
    .replace(/\\/g, "\\\\")
    .replace(/#/g, "\\#")
    .replace(/\[/g, "\\[")
    .replace(/\]/g, "\\]")
    .replace(/@/g, "\\@")
    .replace(/</g, "\\<")
    .replace(/>/g, "\\>")
    .replace(/\*/g, "\\*")
    .replace(/_/g, "\\_");
}

function renderInline(value: string) {
  return escapeTypst(value || "");
}

function normalizeUrl(value?: string) {
  if (!value) return "";
  return value.startsWith("http") ? value : `https://${value}`;
}

function renderResume(draft: Draft) {
  const contact = draft.contact || { name: "Candidate Name" };
  const linkedinLabel = (contact.linkedin || "").replace(/^https?:\/\//, "");
  const githubLabel = (contact.github || "").replace(/^https?:\/\//, "");
  const links = [
    contact.linkedin ? `#link("${escapeTypst(normalizeUrl(contact.linkedin))}")[#text(fill: theme-blue)[${escapeTypst(linkedinLabel)}]]` : "",
    contact.github ? `#link("${escapeTypst(normalizeUrl(contact.github))}")[#text(fill: theme-blue)[${escapeTypst(githubLabel)}]]` : "",
  ].filter(Boolean).join(" #text[ | ] ");
  const details = [contact.location, contact.email].filter(Boolean).map((item) => escapeTypst(item || "")).join(" | ");
  const header = [
    "#align(center)[",
    `  #text(fill: theme-blue, weight: "bold", size: 14pt)[${escapeTypst(contact.name || "Candidate Name")}]`,
    details ? `  #text(fill: black)[ | ${details} |]` : "",
    details ? "  #linebreak()" : "",
    links ? `  ${links}` : "",
    "  #v(-4pt)",
    "  #line(length: 100%, stroke: 0.5pt + gray)",
    "]",
  ].filter(Boolean).join("\n");

  const educationParts = [draft.education?.school, draft.education?.degree, draft.education?.dates ? `#emph[${escapeTypst(draft.education.dates)}]` : ""].filter(Boolean).join(" | ");
  const educationBullets = (draft.education?.bullets || []).map((item) => `#bullet[${renderInline(item)}]`).join("\n");
  const projectBlocks = (draft.projects || []).slice(0, 3).map((project) => {
    const title = [project.title, ...(project.stack || []).slice(0, 2), project.dates || ""].filter(Boolean).join(" | ");
    const bullets = (project.bullets || []).slice(0, 3).map((item) => `#bullet[${renderInline(item)}]`).join("\n");
    return `#project[${escapeTypst(title)}]\n${bullets}`;
  }).join("\n#v(0.45em)\n");
  const skills = (draft.skills || []).slice(0, 4).map((bucket) => `#bullet[#strong[${escapeTypst(bucket.category)}:] ${(bucket.items || []).slice(0, 6).map(renderInline).join(", ")}]`).join("\n");

  return `#set page(
  paper: "us-letter",
  margin: (x: 2.54cm, y: 2.00cm),
)

#set text(font: "DejaVu Sans", size: 10pt, fill: black)
#set par(leading: 0.5em, justify: true, spacing: 0pt)
#set block(spacing: 6pt)

#let theme-blue = rgb("#00508C")

#let section(title) = {
  stack(
    dir: ttb,
    spacing: 1pt,
    text(fill: theme-blue, weight: "bold", size: 11pt)[#upper(title)],
    line(length: 100%, stroke: 0.5pt + theme-blue),
  )
  v(4pt)
}

#let project(title) = {
  v(4pt)
  text(fill: theme-blue, weight: "bold")[#title]
}

#let bullet(content) = {
  grid(
    columns: (12pt, 1fr),
    gutter: 0pt,
    align: (right, left),
    [•#h(4pt)],
    content
  )
}

${header}

#v(8pt)

#section("Profile")
${renderInline(draft.profile)}

#section("Education")
${educationParts}
${educationBullets}

#v(6pt)

#section("Projects")
${projectBlocks}

#v(6pt)

#section("Skills")
${skills}
`;
}

function extractContact(profileText: string) {
  const result: Record<string, string> = {};
  for (const line of profileText.split(/\r?\n/)) {
    const match = line.trim().match(/^\*\s*([^:]+):\s*(.+)$/);
    if (match) result[match[1].toLowerCase()] = match[2].trim();
  }
  return result;
}

function fallbackDraft(body: any): Draft {
  const contact = extractContact(body.candidate_profile || "");
  let projects: Project[] = [];
  let skills: Record<string, string[]> = {};
  try { projects = JSON.parse(body.projects_json || "[]"); } catch { projects = []; }
  try { skills = JSON.parse(body.skills_json || "{}"); } catch { skills = {}; }
  return {
    contact: {
      name: contact.name || "Candidate Name",
      email: contact.email || "",
      location: contact.location || "",
      linkedin: contact.linkedin || "",
      github: contact.github || "",
    },
    headline: body.role_focus || "",
    profile: "Candidate with relevant software project experience aligned to the pasted job description.",
    education: { school: "", degree: "", dates: "", bullets: [] },
    projects: projects.slice(0, 3).map((p) => ({ title: p.title, stack: p.stack || [], dates: p.dates || "", bullets: (p.facts || []).slice(0, 3) })),
    skills: Object.entries(skills).map(([category, items]) => ({ category, items: Array.isArray(items) ? items.map(String) : [] })),
    fit_summary: "Fallback draft generated from submitted structured data.",
    keywords: [],
    job_title: body.role_focus || "",
  };
}

function generationPrompt(body: any) {
  return `Create a one-page CV JSON object from the candidate data and job description. Use only verified facts from the candidate input. Do not invent employers, metrics, dates, awards, or tools.

Return one JSON object with this exact shape:
{
  "contact": {"name":"", "email":"", "location":"", "linkedin":"", "github":""},
  "headline": "",
  "profile": "",
  "education": {"school":"", "degree":"", "dates":"", "bullets":[]},
  "projects": [{"title":"", "stack":[], "dates":"", "bullets":[]}],
  "skills": [{"category":"", "items":[]}],
  "fit_summary": "",
  "keywords": [],
  "job_title": ""
}

Candidate profile:
${body.candidate_profile}

Projects JSON:
${body.projects_json || "[]"}

Skills JSON:
${body.skills_json || "{}"}

Rules:
${body.rules || "Keep every bullet factual and concise."}

Role focus: ${body.role_focus || "general"}

Job description:
${body.job_description}`;
}

function scorePrompt(body: any, draft: Draft) {
  return `Score this generated CV for the job. Return one JSON object with: quality_score number 0-100, match_score number 0-100, score_band string, decision approve or revise, summary string, strengths array, gaps array, recommendations array, revision_brief string.

Set score_band to exactly one of: "Strong" (85-100), "Good" (70-84), or "Moderate" (0-69), based on the lower of quality_score and match_score.

Job description:
${body.job_description}

Draft JSON:
${JSON.stringify(draft)}`;
}

async function callGemini(prompt: string, apiKey: string, maxTokens: number) {
  const model = process.env.STATELESS_MODEL_NAME || "gemini-2.5-flash";
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: "You are a precise resume tailoring engine. Return valid JSON only." }] },
      contents: [{ role: "user", parts: [{ text: prompt }] }],
      generationConfig: { responseMimeType: "application/json", maxOutputTokens: maxTokens },
    }),
  });
  if (!response.ok) throw new Error(`Gemini request failed: ${response.status} ${await response.text()}`);
  const payload = await response.json();
  const content = payload?.candidates?.[0]?.content?.parts?.map((part: { text?: string }) => part.text || "").join("");
  if (!content) throw new Error("Gemini returned an empty response.");
  return JSON.parse(content);
}

async function compileTypst(source: string) {
  const compiler = NodeCompiler.create();
  const pdf = await compiler.pdf({ mainFileContent: source });
  compiler.evictCache(10);
  return Buffer.from(pdf);
}

export const config = {
  maxDuration: 300,
};

export default async function handler(req: any, res: any) {
  if (req.method !== "POST") return jsonResponse(res, 405, { detail: "Method not allowed." });
  const rawLength = Number(req.headers["content-length"] || 0);
  if (rawLength > MAX_PAYLOAD_BYTES) return jsonResponse(res, 413, { detail: "Request is too large for this stateless Vercel endpoint." });

  try {
    const body = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
    if (!body?.candidate_profile || body.candidate_profile.length < 20) return jsonResponse(res, 422, { detail: "candidate_profile is required." });
    if (!body?.job_description || body.job_description.length < 20) return jsonResponse(res, 422, { detail: "job_description is required." });
    const sizeError = validateInputSize(body);
    if (sizeError) return jsonResponse(res, 413, { detail: sizeError });
    const apiKey = process.env.FLASH_API_KEY;
    if (!apiKey) return jsonResponse(res, 500, { detail: "FLASH_API_KEY is not configured." });
    const rateLimit = await consumeRateLimits(req);
    if (!rateLimit.allowed) return jsonResponse(res, 429, { detail: rateLimit.detail });

    let draft: Draft;
    try {
      draft = await callGemini(generationPrompt(body), apiKey, MAX_GENERATION_OUTPUT_TOKENS) as Draft;
    } catch (error) {
      draft = fallbackDraft(body);
    }
    const typstSource = renderResume(draft);
    const pdfBuffer = await compileTypst(typstSource);
    if (pdfBuffer.byteLength > MAX_OUTPUT_BYTES) return jsonResponse(res, 413, { detail: "Generated PDF is too large for Vercel response limits." });

    let scoreReport: ScoreReport | null = null;
    let scoreError: string | null = null;
    try {
      scoreReport = await callGemini(scorePrompt(body, draft), apiKey, MAX_SCORE_OUTPUT_TOKENS) as ScoreReport;
    } catch {
      scoreError = "Gemini scoring was unavailable for this run.";
    }

    return jsonResponse(res, 200, {
      pdf_base64: pdfBuffer.toString("base64"),
      typst_source: typstSource,
      page_count: 1,
      draft,
      score_report: scoreReport,
      score_error: scoreError,
      fit_summary: draft.fit_summary || null,
      compile_logs: [],
      output_basename: safePart(body.output_basename || "tailored-resume"),
      model_name: process.env.STATELESS_MODEL_NAME || "gemini-2.5-flash",
    });
  } catch {
    return jsonResponse(res, 500, { detail: "Generation failed." });
  }
}
