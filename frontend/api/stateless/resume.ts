import { NodeCompiler } from "@myriaddreamin/typst-ts-node-compiler";
import { DEFAULT_RESUME_TEMPLATE } from "./resume-template.js";

const DENSITIES = ["spacious", "comfortable", "compact"] as const;
const DENSITY_RULE = /^#let resume-density = "(spacious|comfortable|compact)"$/m;

function normalizeDensity(value: unknown): typeof DENSITIES[number] {
  const density = typeof value === "string" ? value.trim().toLowerCase() : "";
  return DENSITIES.find((preset) => preset === density) || "compact";
}

export function* resumeLayoutVariants(source: string) {
  const match = DENSITY_RULE.exec(source);
  if (!source.includes("// cv-docker: managed-resume-layout-v1") || !match) {
    yield source;
    return;
  }
  const start = DENSITIES.indexOf(normalizeDensity(match[1]));
  for (const density of DENSITIES.slice(start)) {
    yield source.replace(DENSITY_RULE, () => `#let resume-density = "${density}"`);
  }
}

export function compileResume(
  source: string,
  compiler: Pick<NodeCompiler, "compile" | "pdf" | "evictCache"> = NodeCompiler.create(),
) {
  const logs: string[] = [];
  try {
    let chosenSource = source;
    let document;
    for (const candidate of resumeLayoutVariants(source)) {
      const result = compiler.compile({ mainFileContent: candidate });
      if (result.hasError() || !result.result) throw new Error("Typst compilation failed.");
      document = result.result;
      chosenSource = candidate;
      logs.push(`spacing=${DENSITY_RULE.exec(candidate)?.[1] || "custom"}, page_count=${document.numOfPages}`);
      if (document.numOfPages === 1) break;
    }
    if (!document) throw new Error("Typst produced no document.");
    return { source: chosenSource, pdf: Buffer.from(compiler.pdf(document)), pageCount: document.numOfPages, logs };
  } finally {
    compiler.evictCache(10);
  }
}

export type Draft = {
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

function escapeTypst(value: string) {
  return String(value || "")
    .replace(/\\/g, "\\\\")
    .replace(/#/g, "\\#")
    .replace(/\[/g, "\\[")
    .replace(/\]/g, "\\]")
    .replace(/@/g, "\\@")
    .replace(/\$/g, "\\$")
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

export function renderResume(draft: Draft, layoutDensity: unknown = "comfortable") {
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
    "  #block(above: 3pt, below: 0pt)[#line(length: 100%, stroke: 0.5pt + gray)]",
    "]",
  ].filter(Boolean).join("\n");

  const educationParts = [renderInline(draft.education?.school || ""), renderInline(draft.education?.degree || ""), draft.education?.dates ? `#emph[${escapeTypst(draft.education.dates)}]` : ""].filter(Boolean).join(" | ");
  const educationBullets = (draft.education?.bullets || []).filter((item) => item.trim()).map((item) => `#bullet[${renderInline(item)}]`).join("\n");
  const projectBlocks = (draft.projects || []).filter((project) => project.bullets?.some((item) => item.trim())).slice(0, 3).map((project, index) => {
    const title = [project.title, ...(project.stack || []).slice(0, 2), project.dates || ""].filter(Boolean).join(" | ");
    const bullets = (project.bullets || []).filter((item) => item.trim()).slice(0, 3).map((item) => `#bullet[${renderInline(item)}]`).join("\n");
    return `#project${index === 0 ? "(first: true)" : ""}[${escapeTypst(title)}]\n${bullets}`;
  }).join("\n\n");
  const skills = (draft.skills || []).filter((bucket) => bucket.items?.some((item) => item.trim())).slice(0, 4).map((bucket) => `#bullet[#strong[${escapeTypst(bucket.category)}:] ${(bucket.items || []).filter((item) => item.trim()).slice(0, 6).map(renderInline).join(", ")}]`).join("\n");

  const sections = [
    draft.profile?.trim() ? `#section("Profile")\n${renderInline(draft.profile)}` : "",
    educationParts || educationBullets ? `#section("Education")\n${educationParts}\n${educationBullets}` : "",
    projectBlocks ? `#section("Projects")\n${projectBlocks}` : "",
    skills ? `#section("Skills")\n${skills}` : "",
  ].filter(Boolean);
  const density = normalizeDensity(layoutDensity);
  return DEFAULT_RESUME_TEMPLATE
    .replace("{{CONTACT_HEADER}}", () => header)
    .replace("{{PROFILE_SECTION}}", () => sections.shift() || "")
    .replace("{{BODY_CONTENT}}", () => sections.join("\n\n"))
    .replace("{{LAYOUT_DENSITY}}", density);
}
