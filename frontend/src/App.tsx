import { FormEvent, useEffect, useState, type CSSProperties } from "react";

type GenerateResponse = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "stopped";
};

type JobMetadata = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "stopped";
  stage?: string | null;
  attempts: number;
  page_count?: number | null;
  pdf_url?: string | null;
  typst_url?: string | null;
  fit_summary?: string | null;
  review_summary?: string | null;
  quality_score?: number | null;
  match_score?: number | null;
  score_band?: string | null;
  keywords: string[];
  recommendations: string[];
  error?: string | null;
  compile_logs: string[];
};

type RuntimeConfig = {
  llm_provider: string;
  model_name: string;
  live_model_available: boolean;
  demo_mode_enabled: boolean;
  profile_dir: string;
  output_dir: string;
  output_basename: string;
  personalized_profile_phrase_configured: boolean;
  personalized_forbidden_rule_configured: boolean;
};

type PersonalizationOptions = {
  required_profile_phrase: string;
  required_profile_recommendation: string;
  forbidden_content_regex: string;
  forbidden_content_gap: string;
  forbidden_content_recommendation: string;
  fixed_education_typst: string;
  contact_header_typst: string;
  layout_density: string;
  default_profile_text: string;
  extra_prompt_notes: string;
};

type ProfileSummary = {
  profile_id: string;
  display_name: string;
  active: boolean;
  profile_dir: string;
  exists: boolean;
};

type ProfileBundle = {
  profile_id: string;
  display_name: string;
  profile_dir: string;
  exists: boolean;
  master_profile: string;
  projects_json: string;
  skills_json: string;
  rules: string;
  research_guidelines: string;
  personalization: PersonalizationOptions;
  template_exists: boolean;
};

type AppSettings = {
  llm_provider: string;
  model_name: string;
  reasoning_effort: string;
  abacus_base_url: string;
  output_basename: string;
  enable_demo_mode: boolean;
  openai_api_key_configured: boolean;
  abacus_api_key_configured: boolean;
};

type DraftResponse = {
  summary: string;
  profile: ProfileBundle;
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

type StatelessGenerateResponse = {
  pdf_base64: string;
  typst_source: string;
  page_count: number;
  draft: unknown;
  score_report?: ScoreReport | null;
  compile_logs: string[];
  output_basename: string;
  model_name: string;
};

type OutputSummary = {
  output_dir: string;
  total_jobs: number;
  terminal_jobs: number;
  active_jobs: number;
  deleted_jobs: number;
  deleted_job_ids: string[];
  retained_jobs: number;
  bytes_before: number;
  bytes_after: number;
};

type JobListItem = {
  job_id: string;
  status: string;
  label: string;
  job_title: string;
  created_at: string;
};

const telegramSetupPrompt = `I am configuring a local Docker CV tailoring app with an optional Telegram bot. Guide me step by step. Ask me for my BotFather token, my numeric Telegram user id, and whether I run Docker Compose locally or on a server. Then help me set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, and API_URL=http://api:8000 in .env, restart docker compose, verify the bot responds, and explain how to rotate the token if I accidentally expose it. Do not ask me to paste secrets into a public chat.`;

const cvDataPreparationPrompt = `You are helping me prepare structured private CV data for a stateless CV tailoring tool. I will paste my resume, LinkedIn text, project notes, portfolio notes, education, skills, and preferences.

Your job: extract only verified facts from my text and return one JSON object. Do not invent employers, dates, degrees, awards, links, metrics, tools, or results. If something is unclear, omit it or put it in notes_for_review.

Return exactly this JSON shape:
{
  "candidate_profile": "# Candidate Profile\n\n## Contact\n* Name: ...\n* Email: ...\n* Location: ...\n* LinkedIn: ...\n* GitHub: ...\n\n## Summary Facts\n* ...\n\n## Education\n* School\n* Degree\n* Dates\n* Coursework, honors, or relevant notes",
  "projects_json": [
    {
      "title": "Project name",
      "dates": "YYYY or date range",
      "stack": ["Tool", "Language", "Framework"],
      "cv_status": "active",
      "facts": [
        "Verified project fact with action and technical detail",
        "Verified project fact with method, tool, or constraint",
        "Verified project fact with outcome only if explicitly provided"
      ]
    }
  ],
  "skills_json": {
    "Languages": ["..."],
    "Frameworks": ["..."],
    "Tools": ["..."],
    "Practices": ["..."]
  },
  "rules": "Short factual writing preferences for CV generation. Include constraints such as no invented metrics, preferred role targets, words to avoid, or required phrases.",
  "notes_for_review": ["Anything uncertain that I should verify before using this data"]
}

Make the output valid JSON only. No markdown fences. No explanation outside the JSON.`;

function generationProgress(job: JobMetadata | null): number {
  if (!job) return 0;
  if (job.status === "completed") return 100;
  if (job.status === "failed" || job.status === "stopped") return 100;
  const stage = job.stage || job.status;
  if (stage === "queued") return 8;
  if (stage === "loading_profile") return 16;
  if (stage.startsWith("gen_attempt_")) return Math.min(42 + job.attempts * 8, 62);
  if (stage.startsWith("compile_attempt_")) return Math.min(60 + job.attempts * 8, 76);
  if (stage.startsWith("scr_attempt_")) return Math.min(76 + job.attempts * 7, 92);
  if (stage.includes("approved_draft")) return 72;
  if (stage.includes("compressing")) return 84;
  return job.status === "running" ? 24 : 8;
}

function progressLabel(job: JobMetadata | null): string {
  if (!job) return "Idle";
  if (job.status === "completed") return "Completed";
  if (job.status === "failed") return "Failed";
  if (job.status === "stopped") return "Stopped";
  return job.stage?.replaceAll("_", " ") || job.status;
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "";
const statelessOnly = import.meta.env.VITE_STATELESS_ONLY === "true";
const demoVideoUrl = import.meta.env.VITE_DEMO_VIDEO_URL || "/product-walkthrough-20260713.mp4";
const githubProjectUrl = "https://github.com/GuyvDev/CV-Tailor";
const marketingCopy = {
  eyebrow: "AI-powered CV tailoring",
  headline: "Build a CV that gets you noticed.",
  supporting: "Paste your experience and a job description. CV Tailor creates a focused, role-specific resume and exports a polished PDF with editable Typst source.",
  showcaseTitle: "Tailor every application",
  showcaseText: "CV Tailor evaluates the role, reshapes your strongest verified experience, scores the result, and produces a recruiter-ready resume.",
};

const roleOptions = [
  { label: "General", value: "" },
  { label: "Systems", value: "systems" },
  { label: "AI", value: "ai" },
  { label: "Software", value: "software" },
  { label: "Architecture", value: "architecture" },
];

const emptyAppSettings: AppSettings = {
  llm_provider: "openai",
  model_name: "gpt-5-mini",
  reasoning_effort: "low",
  abacus_base_url: "https://routellm.abacus.ai/v1",
  output_basename: "tailored-resume",
  enable_demo_mode: true,
  openai_api_key_configured: false,
  abacus_api_key_configured: false,
};

const emptyPersonalization: PersonalizationOptions = {
  required_profile_phrase: "",
  required_profile_recommendation: "",
  forbidden_content_regex: "",
  forbidden_content_gap: "",
  forbidden_content_recommendation: "",
  fixed_education_typst: "",
  contact_header_typst: "",
  layout_density: "compact",
  default_profile_text: "",
  extra_prompt_notes: "",
};

const emptyProfile: ProfileBundle = {
  profile_id: "default",
  display_name: "Default",
  profile_dir: "",
  exists: false,
  master_profile: "",
  projects_json: "[]",
  skills_json: "{}",
  rules: "",
  research_guidelines: "",
  personalization: emptyPersonalization,
  template_exists: false,
};

export function App() {
  const [activeView, setActiveView] = useState<"generate" | "stateless" | "personalize">(statelessOnly ? "stateless" : "generate");
  const [jobDescription, setJobDescription] = useState("");
  const [roleFocus, setRoleFocus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [job, setJob] = useState<JobMetadata | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [profile, setProfile] = useState<ProfileBundle>(emptyProfile);
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [newProfileId, setNewProfileId] = useState("");
  const [newProfileName, setNewProfileName] = useState("");
  const [appSettings, setAppSettings] = useState<AppSettings>(emptyAppSettings);
  const [openaiApiKey, setOpenaiApiKey] = useState("");
  const [abacusApiKey, setAbacusApiKey] = useState("");
  const [profileStatus, setProfileStatus] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [setupInput, setSetupInput] = useState("");
  const [draftSummary, setDraftSummary] = useState<string | null>(null);
  const [isDraftingProfile, setIsDraftingProfile] = useState(false);
  const [statelessPreparedJson, setStatelessPreparedJson] = useState("");
  const [statelessPreparedStatus, setStatelessPreparedStatus] = useState<string | null>(null);
  const [statelessCandidate, setStatelessCandidate] = useState("");
  const [statelessProjects, setStatelessProjects] = useState("[]");
  const [statelessSkills, setStatelessSkills] = useState("{}");
  const [statelessRules, setStatelessRules] = useState("Keep every bullet factual and grounded in the candidate input. Do not invent employers, metrics, dates, degrees, or tools.");
  const [statelessJob, setStatelessJob] = useState("");
  const [statelessRoleFocus, setStatelessRoleFocus] = useState("");
  const [statelessOutputName, setStatelessOutputName] = useState("tailored-resume");
  const [statelessResult, setStatelessResult] = useState<StatelessGenerateResponse | null>(null);
  const [statelessError, setStatelessError] = useState<string | null>(null);
  const [isGeneratingStateless, setIsGeneratingStateless] = useState(false);
  const [outputSummary, setOutputSummary] = useState<OutputSummary | null>(null);
  const [cleanupDays, setCleanupDays] = useState(30);
  const [cleanupStatus, setCleanupStatus] = useState<string | null>(null);
  const [isCleaningOutputs, setIsCleaningOutputs] = useState(false);
  const [jobList, setJobList] = useState<JobListItem[]>([]);
  const [jobSearch, setJobSearch] = useState("");

  function resetJob() {
    setJob(null);
    setError(null);
  }

  async function refreshSetup() {
    const [configResponse, profileResponse, appSettingsResponse, profilesResponse, outputResponse, jobsResponse] = await Promise.all([
      fetch(`${apiBaseUrl}/api/config`),
      fetch(`${apiBaseUrl}/api/profile`),
      fetch(`${apiBaseUrl}/api/app-settings`),
      fetch(`${apiBaseUrl}/api/profiles`),
      fetch(`${apiBaseUrl}/api/outputs/summary`),
      fetch(`${apiBaseUrl}/api/jobs`),
    ]);
    if (configResponse.ok) {
      setConfig(await configResponse.json());
    }
    if (profileResponse.ok) {
      setProfile(await profileResponse.json());
    }
    if (appSettingsResponse.ok) {
      setAppSettings(await appSettingsResponse.json());
    }
    if (profilesResponse.ok) {
      setProfiles(await profilesResponse.json());
    }
    if (outputResponse.ok) {
      setOutputSummary(await outputResponse.json());
    }
    if (jobsResponse.ok) {
      setJobList(await jobsResponse.json());
    }
  }

  useEffect(() => {
    refreshSetup().catch(() => undefined);
  }, []);

  async function stopCurrentJob() {
    if (!job || (job.status !== "queued" && job.status !== "running")) {
      return;
    }

    try {
      const response = await fetch(`${apiBaseUrl}/api/jobs/${job.job_id}/stop`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error("Stop request failed.");
      }
      const payload: JobMetadata = await response.json();
      setJob(payload);
    } catch (stopError) {
      setError(
        stopError instanceof Error
          ? stopError.message
          : "Unknown stop error.",
      );
    }
  }

  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) {
      return;
    }

    const interval = window.setInterval(async () => {
      const response = await fetch(`${apiBaseUrl}/api/jobs/${job.job_id}`);
      if (!response.ok) {
        return;
      }
      const data: JobMetadata = await response.json();
      setJob(data);
    }, 2000);

    return () => window.clearInterval(interval);
  }, [job]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    setJob(null);

    try {
      const response = await fetch(`${apiBaseUrl}/api/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          job_description: jobDescription,
          role_focus: roleFocus || null,
          template: "default",
        }),
      });

      if (!response.ok) {
        throw new Error("Generation request failed.");
      }

      const payload: GenerateResponse = await response.json();
      const statusResponse = await fetch(`${apiBaseUrl}/api/jobs/${payload.job_id}`);
      const statusPayload: JobMetadata = await statusResponse.json();
      setJob(statusPayload);
    } catch (submissionError) {
      setError(
        submissionError instanceof Error
          ? submissionError.message
          : "Unknown submission error.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSavingProfile(true);
    setProfileError(null);
    setProfileStatus(null);

    try {
      const response = await fetch(`${apiBaseUrl}/api/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          master_profile: profile.master_profile,
          projects_json: profile.projects_json,
          skills_json: profile.skills_json,
          rules: profile.rules,
          research_guidelines: profile.research_guidelines,
          personalization: profile.personalization,
          initialize_templates: true,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "Profile save failed.");
      }
      setProfile(await response.json());
      setProfileStatus("Saved to your private profile directory.");
    } catch (saveError) {
      setProfileError(saveError instanceof Error ? saveError.message : "Unknown save error.");
    } finally {
      setIsSavingProfile(false);
    }
  }

  async function switchProfile(profileId: string) {
    setProfileError(null);
    setProfileStatus(null);
    const response = await fetch(`${apiBaseUrl}/api/profiles/active`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      setProfileError(payload?.detail || "Profile switch failed.");
      return;
    }
    await refreshSetup();
    setProfileStatus("Active profile changed.");
  }

  async function createProfile() {
    setProfileError(null);
    setProfileStatus(null);
    const response = await fetch(`${apiBaseUrl}/api/profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile_id: newProfileId,
        display_name: newProfileName,
        copy_example: true,
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      setProfileError(payload?.detail || "Profile creation failed.");
      return;
    }
    setNewProfileId("");
    setNewProfileName("");
    await refreshSetup();
    setProfileStatus("New profile created and selected.");
  }

  async function deleteProfile(profileId: string) {
    setProfileError(null);
    setProfileStatus(null);
    const response = await fetch(`${apiBaseUrl}/api/profiles/${encodeURIComponent(profileId)}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      setProfileError(payload?.detail || "Profile deletion failed.");
      return;
    }
    await refreshSetup();
    setProfileStatus("Profile deleted.");
  }

  async function saveAppSettings() {
    setIsSavingSettings(true);
    setProfileError(null);
    setProfileStatus(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/app-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...appSettings,
          openai_api_key: openaiApiKey,
          abacus_api_key: abacusApiKey,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "App settings save failed.");
      }
      setAppSettings(await response.json());
      setOpenaiApiKey("");
      setAbacusApiKey("");
      setProfileStatus("App settings saved locally.");
      await refreshSetup();
    } catch (settingsError) {
      setProfileError(settingsError instanceof Error ? settingsError.message : "Unknown settings error.");
    } finally {
      setIsSavingSettings(false);
    }
  }

  async function draftProfileFromNotes() {
    setIsDraftingProfile(true);
    setProfileError(null);
    setProfileStatus(null);
    setDraftSummary(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/profile/ai-draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_text: setupInput }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "AI setup draft failed.");
      }
      const payload: DraftResponse = await response.json();
      setProfile(payload.profile);
      setDraftSummary(payload.summary);
      setProfileStatus("AI draft loaded into the editor. Review it, then save if it looks right.");
    } catch (draftError) {
      setProfileError(draftError instanceof Error ? draftError.message : "Unknown AI draft error.");
    } finally {
      setIsDraftingProfile(false);
    }
  }

  function setPersonalization(update: Partial<PersonalizationOptions>) {
    setProfile({
      ...profile,
      personalization: { ...profile.personalization, ...update },
    });
  }

  function downloadBase64File(filename: string, base64: string, mimeType: string) {
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: mimeType }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function downloadTextFile(filename: string, content: string) {
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function loadPreparedCvData() {
    setStatelessError(null);
    setStatelessPreparedStatus(null);
    try {
      const payload = JSON.parse(statelessPreparedJson);
      const projects = Array.isArray(payload.projects_json) ? JSON.stringify(payload.projects_json, null, 2) : String(payload.projects_json || "[]");
      const skills = typeof payload.skills_json === "object" && payload.skills_json !== null ? JSON.stringify(payload.skills_json, null, 2) : String(payload.skills_json || "{}");
      setStatelessCandidate(String(payload.candidate_profile || ""));
      setStatelessProjects(projects);
      setStatelessSkills(skills);
      setStatelessRules(String(payload.rules || "Keep every bullet factual and grounded in the candidate input. Do not invent employers, metrics, dates, degrees, or tools."));
      setStatelessPreparedStatus("Prepared CV data loaded. Review it below, then paste a job description and generate.");
    } catch (parseError) {
      setStatelessError(parseError instanceof Error ? `Prepared JSON is invalid: ${parseError.message}` : "Prepared JSON is invalid.");
    }
  }

  async function generateStateless(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsGeneratingStateless(true);
    setStatelessError(null);
    setStatelessResult(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/stateless/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate_profile: statelessCandidate,
          job_description: statelessJob,
          projects_json: statelessProjects || "[]",
          skills_json: statelessSkills || "{}",
          role_focus: statelessRoleFocus || null,
          rules: statelessRules,
          research_guidelines: "Score for role fit, factual grounding, one-page density, ATS-readable wording, and recruiter scan clarity.",
          personalization: { ...emptyPersonalization, layout_density: "compact" },
          output_basename: statelessOutputName || "tailored-resume",
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "Stateless generation failed.");
      }
      setStatelessResult(await response.json());
    } catch (generateError) {
      setStatelessError(generateError instanceof Error ? generateError.message : "Unknown stateless generation error.");
    } finally {
      setIsGeneratingStateless(false);
    }
  }


  function formatBytes(value: number): string {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
  }

  function jsonStatus(value: string, expected: "array" | "object"): string {
    try {
      const parsed = JSON.parse(value || (expected === "array" ? "[]" : "{}"));
      if (expected === "array" && !Array.isArray(parsed)) return "Must be a JSON array.";
      if (expected === "object" && (Array.isArray(parsed) || parsed === null || typeof parsed !== "object")) return "Must be a JSON object.";
      return "Valid JSON.";
    } catch (jsonError) {
      return jsonError instanceof Error ? jsonError.message : "Invalid JSON.";
    }
  }

  async function cleanupOutputs(deleteAllTerminal = false) {
    setIsCleaningOutputs(true);
    setCleanupStatus(null);
    setProfileError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/outputs/cleanup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          older_than_days: cleanupDays,
          include_failed: true,
          include_stopped: true,
          delete_all_terminal: deleteAllTerminal,
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "Output cleanup failed.");
      }
      const summary: OutputSummary = await response.json();
      setOutputSummary(summary);
      setCleanupStatus(`Deleted ${summary.deleted_jobs} job archive${summary.deleted_jobs === 1 ? "" : "s"}.`);
      await refreshSetup();
    } catch (cleanupError) {
      setProfileError(cleanupError instanceof Error ? cleanupError.message : "Unknown cleanup error.");
    } finally {
      setIsCleaningOutputs(false);
    }
  }

  const progress = generationProgress(job);
  const currentProfile = profiles.find((item) => item.profile_id === profile.profile_id);
  const filteredJobs = jobList.filter((item) => {
    const haystack = `${item.job_id} ${item.status} ${item.label} ${item.job_title}`.toLowerCase();
    return haystack.includes(jobSearch.trim().toLowerCase());
  });
  const setupItems = [
    { label: "Profile", done: profile.exists },
    { label: "Model", done: Boolean(config?.live_model_available || config?.demo_mode_enabled) },
    { label: "Template", done: profile.template_exists },
    { label: "Outputs", done: Boolean(outputSummary) },
  ];
  const heroMetrics = [
    { value: profile.exists ? "Ready" : "New", label: "Profile" },
    { value: String(job?.quality_score ?? "AI"), label: "Quality" },
    { value: String(job?.page_count ?? 1), label: "Page goal" },
  ];

  return (
    <main className={statelessOnly ? "shell stateless-shell" : "shell"} id="top">
      <header className="site-header">
        <a className="brand" href="#top" aria-label="CV Tailor home">CV<span>Tailor</span></a>
        <nav className="site-nav" aria-label="Page sections">
          <a href="#showcase">How it works</a>
          <a href="#workspace">Generate</a>
          <a href="#privacy">Privacy</a>
        </nav>
        <a className="header-cta" href="#workspace">Generate your CV</a>
      </header>
      <section className="hero">
        <div className="resume-backdrop" aria-hidden="true">
          <div className="resume-sheet">
            <span className="resume-kicker">CANDIDATE NAME</span><i /><b>Professional summary</b><em /><em /><b>Selected experience</b><em /><em /><em /><b>Skills &amp; tools</b><em />
          </div>
        </div>
        <div className="hero-layout">
          <div className="hero-copy">
            {!statelessOnly ? (
              <div className="hero-topline">
                <span className="mode-badge stateful">Private stateful</span>
              </div>
            ) : null}
            <p className="eyebrow">{statelessOnly ? marketingCopy.eyebrow : "CV Tailor"}</p>
            <h1>{statelessOnly ? marketingCopy.headline : "Build a CV that helps you land your next job."}</h1>
            <p className="lede">{statelessOnly ? marketingCopy.supporting : "Paste your information, add a job description, and export a polished PDF with editable Typst source."}</p>
            {statelessOnly ? <div className="hero-actions"><a className="primary-link" href="#workspace">Generate your CV <span aria-hidden="true">→</span></a><a className="secondary-link" href="#showcase">View the workflow</a></div> : null}
            {!statelessOnly ? (
              <div className="setup-rail" aria-label="Setup status">
                {setupItems.map((item) => (
                  <span className={item.done ? "setup-chip done" : "setup-chip"} key={item.label}>{item.done ? "Ready" : "Check"} · {item.label}</span>
                ))}
              </div>
            ) : null}
          </div>
          {!statelessOnly ? (
            <div className={`hero-metrics metric-count-${heroMetrics.length}`} aria-label="CV readiness summary">
              {heroMetrics.map((metric) => <div key={metric.label}><strong>{metric.value}</strong><span>{metric.label}</span></div>)}
            </div>
          ) : null}
        </div>
      </section>

      <section className="product-proof" id="showcase" aria-label="CV Tailor product walkthrough">
        <div className="product-proof-copy">
          <p className="eyebrow">See the product in motion</p>
          <h2>{statelessOnly ? marketingCopy.showcaseTitle : "Send a job posting through Telegram and receive a tailored CV."}</h2>
          <p>{statelessOnly ? marketingCopy.showcaseText : "The self-hosted edition lets you securely save your profile and job history and use an approved Telegram bot. Deploy it from GitHub."}</p>
          <a className="github-link" href={githubProjectUrl} rel="noreferrer" target="_blank">{statelessOnly ? "Build your Telegram CV bot" : "Build the Stateful edition from GitHub"}</a>
        </div>
        <div className="demo-video" aria-label="Stateful Telegram workflow demo">
          <video controls playsInline preload="metadata" src={demoVideoUrl}>
            Your browser does not support embedded video.
          </video>
          <div className="video-overlay"><span>CV Tailor</span><strong>From role brief to polished resume.</strong></div>
        </div>
      </section>

      {statelessOnly ? <section className="benefit-grid" aria-label="Core CV Tailor benefits">
        <article><span className="benefit-number">01</span><h2>Tailored to the role</h2><p>Matches your verified experience to the role’s actual needs.</p></article>
        <article><span className="benefit-number">02</span><h2>Scored before export</h2><p>Reviews quality, keyword alignment, recruiter clarity, and one-page fit.</p></article>
        <article><span className="benefit-number">03</span><h2>PDF and editable source</h2><p>Exports a finished PDF and editable Typst source.</p></article>
      </section> : null}

      {!statelessOnly ? (
        <nav className="tabs" aria-label="Workspace views">
          <button className={activeView === "generate" ? "tab active" : "tab"} type="button" onClick={() => setActiveView("generate")}>Generate</button>
          <button className={activeView === "stateless" ? "tab active" : "tab"} type="button" onClick={() => setActiveView("stateless")}>Stateless</button>
          <button className={activeView === "personalize" ? "tab active" : "tab"} type="button" onClick={() => setActiveView("personalize")}>Personalize</button>
        </nav>
      ) : null}

      {activeView === "generate" ? (
        <div className="generation-workspace">
          <section className="panel generation-form-panel">
            <form className="form" onSubmit={onSubmit}>
              <label className="field">
                <span>Role focus</span>
                <select value={roleFocus} onChange={(event) => setRoleFocus(event.target.value)}>
                  {roleOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="field">
                <span>Job description</span>
                <textarea
                  value={jobDescription}
                  onChange={(event) => setJobDescription(event.target.value)}
                  placeholder="Paste the full role description here..."
                  rows={14}
                  required
                />
              </label>

              <button disabled={isSubmitting || jobDescription.trim().length < 20} type="submit">
                {isSubmitting ? "Submitting..." : "Generate tailored CV"}
              </button>
            </form>
          </section>

          <section className="panel result">
            <div className="result-header">
              <h2>Generation status</h2>
              <div className="actions-row">
                {job ? <span className={`badge badge-${job.status}`}>{job.status}</span> : null}
                {job && (job.status === "queued" || job.status === "running") ? (
                  <button className="small-button" type="button" onClick={stopCurrentJob}>Stop</button>
                ) : null}
                {job && (job.status === "completed" || job.status === "failed" || job.status === "stopped") ? (
                  <button className="small-button" type="button" onClick={resetJob}>New resume</button>
                ) : null}
              </div>
            </div>

            {job ? (
              <div className="progress-panel" aria-label="Generation progress">
                <div className={`progress-ring status-${job.status}`} style={{ "--progress": `${progress}%` } as CSSProperties}>
                  <span>{progress}%</span>
                </div>
                <div className="progress-copy">
                  <strong>{progressLabel(job)}</strong>
                  <div className="progress-bar"><span style={{ width: `${progress}%` }} /></div>
                </div>
              </div>
            ) : null}

            {error ? <p className="error">{error}</p> : null}
            {!job ? <p className="muted">No job submitted yet.</p> : null}

            {job ? (
              <div className="result-body">
                <p><strong>Stage:</strong> {job.stage ?? "waiting"}</p>
                <p><strong>Attempts:</strong> {job.attempts}</p>
                <p><strong>Page count:</strong> {job.page_count ?? "pending"}</p>
                {job.fit_summary ? <p><strong>Fit summary:</strong> {job.fit_summary}</p> : null}
                {job.quality_score != null || job.match_score != null ? (
                  <p>
                    <strong>Scores:</strong>{" "}
                    {job.quality_score != null ? `Quality ${job.quality_score}/100` : ""}
                    {job.quality_score != null && job.match_score != null ? " | " : ""}
                    {job.match_score != null ? `Match ${job.match_score}/100` : ""}
                    {job.score_band ? ` | ${job.score_band}` : ""}
                  </p>
                ) : null}
                {job.review_summary ? <p><strong>Score review:</strong> {job.review_summary}</p> : null}
                {job.keywords.length > 0 ? <p><strong>Keywords:</strong> {job.keywords.join(", ")}</p> : null}
                {job.recommendations.length > 0 ? (
                  <div>
                    <strong>Improvement ideas:</strong>
                    <ul>{job.recommendations.map((item) => <li key={item}>{item}</li>)}</ul>
                  </div>
                ) : null}
                {job.error ? <p className="error">{job.error}</p> : null}

                <div className="downloads">
                  {job.pdf_url ? <a href={`${apiBaseUrl}${job.pdf_url}`} rel="noreferrer" target="_blank">Download PDF</a> : null}
                  {job.typst_url ? <a href={`${apiBaseUrl}${job.typst_url}`} rel="noreferrer" target="_blank">Download Typst</a> : null}
                  {job.typst_url ? <a href="https://typst.app/play/" rel="noreferrer" target="_blank">Open Typst playground</a> : null}
                </div>

                {job.pdf_url ? (
                  <div className="pdf-preview">
                    <iframe title="Generated CV preview" src={`${apiBaseUrl}${job.pdf_url}`} />
                  </div>
                ) : null}

                {job.compile_logs.length > 0 ? (
                  <div className="logs">
                    <h3>Compile log</h3>
                    <pre>{job.compile_logs.join("\n\n")}</pre>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>
        </div>
      ) : activeView === "stateless" ? (
        <>
          <section className="workflow-strip" id="privacy" aria-label="Stateless CV workflow">
            <div>
              <span>1</span>
              <strong>Prepare information</strong>
              <p>Use the prompt with your preferred AI.</p>
            </div>
            <div>
              <span>2</span>
              <strong>Review data</strong>
              <p>Paste the JSON and keep only true details.</p>
            </div>
            <div>
              <span>3</span>
              <strong>Generate CV</strong>
              <p>Add the role and download the result.</p>
            </div>
          </section>
          <section className="panel stateless-prep-panel" id="workspace">
            <div className="form">
              <div className="notice privacy-note">
                <strong>Stateless demo:</strong> pasted CV data is used for this generation only. No server profile, job history, or output archive is created. For maximum privacy, run the GitHub project locally or use the included Telegram bot workflow on your own machine.
              </div>

              <div className="subsection prompt-helper">
                <div className="result-header">
                  <h3>Prepare Your CV Data</h3>
                  <button className="small-button" type="button" onClick={() => navigator.clipboard.writeText(cvDataPreparationPrompt)}>Copy prompt</button>
                </div>
                <label className="field">
                  <span>Prompt for ChatGPT, Gemini, Claude, or another AI</span>
                  <textarea className="mono" readOnly rows={12} value={cvDataPreparationPrompt} />
                </label>
              </div>

              <div className="subsection">
                <h3>Paste Prepared Data</h3>
                <label className="field">
                  <span>Prepared CV data JSON</span>
                  <textarea
                    className="mono"
                    value={statelessPreparedJson}
                    onChange={(event) => setStatelessPreparedJson(event.target.value)}
                    rows={12}
                    placeholder="Paste the JSON returned by your AI here. It should include candidate_profile, projects_json, skills_json, and rules."
                  />
                </label>
                <button className="small-button" disabled={statelessPreparedJson.trim().length < 20} type="button" onClick={loadPreparedCvData}>Load prepared data</button>
                {statelessPreparedStatus ? <p className="success">{statelessPreparedStatus}</p> : null}
              </div>
            </div>
          </section>

          <section className="panel stateless-generate-panel">
            <form className="form" onSubmit={generateStateless}>
              <div className="result-header">
                <h2>Generate Tailored CV</h2>
              </div>
              <div className="settings-grid">
                <label className="field">
                  <span>Role focus</span>
                  <select value={statelessRoleFocus} onChange={(event) => setStatelessRoleFocus(event.target.value)}>
                    {roleOptions.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Output name</span>
                  <input value={statelessOutputName} onChange={(event) => setStatelessOutputName(event.target.value)} />
                </label>
              </div>
              <label className="field">
                <span>Candidate profile</span>
                <textarea value={statelessCandidate} onChange={(event) => setStatelessCandidate(event.target.value)} rows={9} required />
              </label>
              <details className="advanced-block">
                <summary>Review structured data</summary>
                <label className="field">
                  <span>Projects JSON</span>
                  <textarea className="mono" value={statelessProjects} onChange={(event) => setStatelessProjects(event.target.value)} rows={8} />
                  <small className={jsonStatus(statelessProjects, "array") === "Valid JSON." ? "field-hint valid" : "field-hint invalid"}>{jsonStatus(statelessProjects, "array")}</small>
                </label>
                <label className="field">
                  <span>Skills JSON</span>
                  <textarea className="mono" value={statelessSkills} onChange={(event) => setStatelessSkills(event.target.value)} rows={6} />
                  <small className={jsonStatus(statelessSkills, "object") === "Valid JSON." ? "field-hint valid" : "field-hint invalid"}>{jsonStatus(statelessSkills, "object")}</small>
                </label>
                <label className="field">
                  <span>Generation rules</span>
                  <textarea value={statelessRules} onChange={(event) => setStatelessRules(event.target.value)} rows={4} />
                </label>
              </details>
              <label className="field">
                <span>Job description</span>
                <textarea value={statelessJob} onChange={(event) => setStatelessJob(event.target.value)} rows={10} placeholder="Paste the role description here..." required />
              </label>
              <button disabled={isGeneratingStateless || statelessCandidate.trim().length < 20 || statelessJob.trim().length < 20} type="submit">
                {isGeneratingStateless ? "Generating..." : "Generate PDF"}
              </button>
            </form>
          </section>

          <section className="panel result">
            <div className="result-header">
              <h2>Stateless result</h2>
              {statelessResult ? <span className="badge badge-completed">{statelessResult.model_name}</span> : null}
            </div>
            {statelessError ? <p className="error">{statelessError}</p> : null}
            {!statelessResult && !statelessError ? <p className="muted">No stateless CV generated yet.</p> : null}
            {statelessResult ? (
              <div className="result-body">
                <p><strong>Page count:</strong> {statelessResult.page_count}</p>
                {statelessResult.score_report ? (
                  <p><strong>Score:</strong> Quality {statelessResult.score_report.quality_score}/100 | Match {statelessResult.score_report.match_score}/100 | {statelessResult.score_report.score_band}</p>
                ) : null}
                {statelessResult.score_report?.summary ? <p><strong>Review:</strong> {statelessResult.score_report.summary}</p> : null}
                <div className="downloads">
                  <button className="small-button" type="button" onClick={() => downloadBase64File(`${statelessResult.output_basename}.pdf`, statelessResult.pdf_base64, "application/pdf")}>Download PDF</button>
                  <button className="small-button" type="button" onClick={() => downloadTextFile(`${statelessResult.output_basename}.typ`, statelessResult.typst_source)}>Download Typst</button>
                  <a href="https://typst.app/play/" rel="noreferrer" target="_blank">Open Typst playground</a>
                </div>
                <div className="pdf-preview">
                  <iframe title="Stateless CV preview" src={`data:application/pdf;base64,${statelessResult.pdf_base64}`} />
                </div>
                {statelessResult.compile_logs.length > 0 ? <pre className="inline-log">{statelessResult.compile_logs.join("\n")}</pre> : null}
              </div>
            ) : null}
          </section>
        </>
      ) : (
        <section className="panel personalize">
          <div className="result-header">
            <h2>Personalize</h2>
            <button className="small-button" type="button" onClick={() => refreshSetup()}>Refresh</button>
          </div>

          <div className="status-grid">
            <div><strong>Active profile</strong><span>{profile.display_name} ({profile.profile_id})</span></div>
            <div><strong>Profile directory</strong><span>{profile.profile_dir || config?.profile_dir || "not loaded"}</span></div>
            <div><strong>Profile files</strong><span>{profile.exists ? "present" : "not created yet"}</span></div>
            <div><strong>Templates</strong><span>{profile.template_exists ? "present" : "will initialize on save"}</span></div>
            <div><strong>Model</strong><span>{config ? `${config.llm_provider} / ${config.model_name}` : "not loaded"}</span></div>
            <div><strong>Model mode</strong><span>{config?.live_model_available ? "live" : config?.demo_mode_enabled ? "demo" : "missing key"}</span></div>
            <div><strong>Output name</strong><span>{config?.output_basename || "tailored-resume"}</span></div>
            <div><strong>Personal rules</strong><span>{config?.personalized_profile_phrase_configured || config?.personalized_forbidden_rule_configured ? "configured" : "optional"}</span></div>
          </div>

          <div className="notice">
            App settings are saved locally in your private profile folder. Stored API keys are never displayed after saving; enter a new key only when you want to replace one.
          </div>

          <div className="subsection storage-block">
            <div className="result-header">
              <h3>Output Retention</h3>
              <button className="small-button" type="button" onClick={() => refreshSetup()}>Refresh storage</button>
            </div>
            <div className="status-grid compact">
              <div><strong>Output directory</strong><span>{outputSummary?.output_dir || config?.output_dir || "not loaded"}</span></div>
              <div><strong>Stored jobs</strong><span>{outputSummary ? `${outputSummary.total_jobs} total / ${outputSummary.active_jobs} active` : "not loaded"}</span></div>
              <div><strong>Archive size</strong><span>{outputSummary ? formatBytes(outputSummary.bytes_after) : "not loaded"}</span></div>
            </div>
            <div className="settings-grid retention-controls">
              <label className="field">
                <span>Delete terminal jobs older than days</span>
                <input min={0} max={3650} type="number" value={cleanupDays} onChange={(event) => setCleanupDays(Number(event.target.value || 0))} />
              </label>
              <div className="actions-row align-end">
                <button className="small-button" disabled={isCleaningOutputs} type="button" onClick={() => cleanupOutputs(false)}>Clean old outputs</button>
                <button className="small-button danger-button" disabled={isCleaningOutputs || !outputSummary?.terminal_jobs} type="button" onClick={() => cleanupOutputs(true)}>Delete terminal outputs</button>
              </div>
            </div>
            {cleanupStatus ? <p className="success">{cleanupStatus}</p> : null}
            <div className="job-history">
              <label className="field">
                <span>Job history search</span>
                <input value={jobSearch} onChange={(event) => setJobSearch(event.target.value)} placeholder="Search id, status, label, or title" />
              </label>
              <div className="job-table" role="table" aria-label="Stored job history">
                {filteredJobs.slice(0, 12).map((item) => (
                  <div className="job-row" role="row" key={item.job_id}>
                    <span>{item.job_id}</span>
                    <span>{item.status}</span>
                    <span>{item.job_title || item.label || "Untitled"}</span>
                    <span>{new Date(item.created_at).toLocaleDateString()}</span>
                  </div>
                ))}
                {filteredJobs.length === 0 ? <p className="muted">No stored jobs match that filter.</p> : null}
              </div>
            </div>
          </div>

          <div className="subsection">
            <h3>Profiles</h3>
            <div className="settings-grid">
              <label className="field wide">
                <span>Active profile</span>
                <select value={profile.profile_id} onChange={(event) => switchProfile(event.target.value)}>
                  {profiles.map((item) => (
                    <option key={item.profile_id} value={item.profile_id}>
                      {item.display_name} ({item.profile_id})
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>New profile id</span>
                <input value={newProfileId} onChange={(event) => setNewProfileId(event.target.value)} placeholder="jane-doe" />
              </label>
              <label className="field">
                <span>Display name</span>
                <input value={newProfileName} onChange={(event) => setNewProfileName(event.target.value)} placeholder="Jane Doe" />
              </label>
            </div>
            <div className="actions-row">
              <button className="small-button" disabled={newProfileId.trim().length < 1} type="button" onClick={createProfile}>Create and switch</button>
              {currentProfile ? <span className="muted">Current: {currentProfile.display_name}</span> : null}
            </div>
            <div className="profile-list">
              {profiles.map((item) => (
                <div className="profile-row" key={item.profile_id}>
                  <div>
                    <strong>{item.display_name}</strong>
                    <span>{item.profile_id}{item.active ? " · active" : ""}</span>
                  </div>
                  <div className="actions-row">
                    {!item.active ? <button className="small-button" type="button" onClick={() => switchProfile(item.profile_id)}>Switch</button> : null}
                    <button
                      className="small-button danger-button"
                      disabled={item.profile_id === "default" || item.active}
                      type="button"
                      onClick={() => deleteProfile(item.profile_id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="subsection">
            <h3>App Settings</h3>
            <div className="settings-grid">
              <label className="field">
                <span>Provider</span>
                <select value={appSettings.llm_provider} onChange={(event) => setAppSettings({ ...appSettings, llm_provider: event.target.value })}>
                  <option value="openai">OpenAI</option>
                  <option value="abacus">Abacus</option>
                </select>
              </label>
              <label className="field">
                <span>Model</span>
                <input value={appSettings.model_name} onChange={(event) => setAppSettings({ ...appSettings, model_name: event.target.value })} placeholder="gpt-5-mini" />
              </label>
              <label className="field">
                <span>Reasoning effort</span>
                <select value={appSettings.reasoning_effort} onChange={(event) => setAppSettings({ ...appSettings, reasoning_effort: event.target.value })}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </label>
              <label className="field">
                <span>Output name</span>
                <input value={appSettings.output_basename} onChange={(event) => setAppSettings({ ...appSettings, output_basename: event.target.value })} placeholder="tailored-resume" />
              </label>
              <label className="field">
                <span>OpenAI API key {appSettings.openai_api_key_configured ? "(saved)" : ""}</span>
                <input value={openaiApiKey} onChange={(event) => setOpenaiApiKey(event.target.value)} placeholder={appSettings.openai_api_key_configured ? "Leave blank to keep saved key" : "Paste key"} type="password" />
              </label>
              <label className="field">
                <span>Abacus API key {appSettings.abacus_api_key_configured ? "(saved)" : ""}</span>
                <input value={abacusApiKey} onChange={(event) => setAbacusApiKey(event.target.value)} placeholder={appSettings.abacus_api_key_configured ? "Leave blank to keep saved key" : "Paste key"} type="password" />
              </label>
              <label className="field wide">
                <span>Abacus base URL</span>
                <input value={appSettings.abacus_base_url} onChange={(event) => setAppSettings({ ...appSettings, abacus_base_url: event.target.value })} />
              </label>
              <label className="check-field">
                <input checked={appSettings.enable_demo_mode} onChange={(event) => setAppSettings({ ...appSettings, enable_demo_mode: event.target.checked })} type="checkbox" />
                <span>Allow demo mode when no live key is available</span>
              </label>
            </div>
            <button className="small-button" disabled={isSavingSettings} type="button" onClick={saveAppSettings}>
              {isSavingSettings ? "Saving..." : "Save app settings"}
            </button>
          </div>

          <div className="subsection">
            <h3>AI Setup Draft</h3>
            <label className="field">
              <span>Paste your rough CV notes, projects, preferences, or current resume text</span>
              <textarea value={setupInput} onChange={(event) => setSetupInput(event.target.value)} rows={8} placeholder="Paste raw notes here. The AI will convert them into profile files and personalization settings for you to review." />
            </label>
            <button className="small-button" disabled={isDraftingProfile || setupInput.trim().length < 20} type="button" onClick={draftProfileFromNotes}>
              {isDraftingProfile ? "Drafting..." : "Draft profile with AI"}
            </button>
            {draftSummary ? <p className="success">{draftSummary}</p> : null}
          </div>

          <div className="subsection guide-block">
            <h3>Telegram Bot Setup</h3>
            <ol>
              <li>Create a bot with BotFather and keep the token private.</li>
              <li>Find your numeric Telegram user id and put it in <code>TELEGRAM_ALLOWED_USER_IDS</code>.</li>
              <li>Set <code>TELEGRAM_BOT_TOKEN</code>, <code>TELEGRAM_ALLOWED_USER_IDS</code>, and <code>API_URL=http://api:8000</code> in <code>.env</code>.</li>
              <li>Run <code>docker compose up --build frontend api typst-compiler telegram-bot</code>.</li>
              <li>Use this Personalize tab to choose the active profile before sending jobs to the bot.</li>
            </ol>
            <label className="field">
              <span>AI helper prompt for Telegram setup</span>
              <textarea className="mono" readOnly rows={5} value={telegramSetupPrompt} />
            </label>
          </div>

          <form className="form" onSubmit={saveProfile}>
            <label className="field">
              <span>Base CV / master profile</span>
              <textarea value={profile.master_profile} onChange={(event) => setProfile({ ...profile, master_profile: event.target.value })} rows={14} />
            </label>
            <label className="field">
              <span>Projects JSON</span>
              <textarea className="mono" value={profile.projects_json} onChange={(event) => setProfile({ ...profile, projects_json: event.target.value })} rows={14} />
              <small className={jsonStatus(profile.projects_json, "array") === "Valid JSON." ? "field-hint valid" : "field-hint invalid"}>{jsonStatus(profile.projects_json, "array")}</small>
            </label>
            <label className="field">
              <span>Skills JSON</span>
              <textarea className="mono" value={profile.skills_json} onChange={(event) => setProfile({ ...profile, skills_json: event.target.value })} rows={10} />
              <small className={jsonStatus(profile.skills_json, "object") === "Valid JSON." ? "field-hint valid" : "field-hint invalid"}>{jsonStatus(profile.skills_json, "object")}</small>
            </label>
            <label className="field">
              <span>Rules</span>
              <textarea value={profile.rules} onChange={(event) => setProfile({ ...profile, rules: event.target.value })} rows={8} />
            </label>
            <label className="field">
              <span>Review guidelines</span>
              <textarea value={profile.research_guidelines} onChange={(event) => setProfile({ ...profile, research_guidelines: event.target.value })} rows={8} />
            </label>

            <div className="subsection">
              <h3>Personalized Rules</h3>
              <label className="field">
                <span>Required profile phrase</span>
                <input
                  value={profile.personalization.required_profile_phrase}
                  onChange={(event) => setPersonalization({ required_profile_phrase: event.target.value })}
                  placeholder="Example: #strong[(Award or certification)]"
                />
              </label>
              <label className="field">
                <span>Recommendation when required phrase is missing</span>
                <textarea
                  value={profile.personalization.required_profile_recommendation}
                  onChange={(event) => setPersonalization({ required_profile_recommendation: event.target.value })}
                  rows={3}
                />
              </label>
              <label className="field">
                <span>Forbidden or duplicate generated-content pattern</span>
                <input
                  className="mono"
                  value={profile.personalization.forbidden_content_regex}
                  onChange={(event) => setPersonalization({ forbidden_content_regex: event.target.value })}
                  placeholder="Regex pattern to remove or penalize"
                />
              </label>
              <label className="field">
                <span>Gap text when that content appears</span>
                <textarea
                  value={profile.personalization.forbidden_content_gap}
                  onChange={(event) => setPersonalization({ forbidden_content_gap: event.target.value })}
                  rows={3}
                />
              </label>
              <label className="field">
                <span>Recommendation for that cleanup rule</span>
                <textarea
                  value={profile.personalization.forbidden_content_recommendation}
                  onChange={(event) => setPersonalization({ forbidden_content_recommendation: event.target.value })}
                  rows={3}
                />
              </label>
              <label className="field">
                <span>Fixed education or honors Typst block</span>
                <textarea
                  className="mono"
                  value={profile.personalization.fixed_education_typst}
                  onChange={(event) => setPersonalization({ fixed_education_typst: event.target.value })}
                  rows={5}
                />
              </label>
              <label className="field">
                <span>CV spacing density</span>
                <select
                  value={profile.personalization.layout_density || "compact"}
                  onChange={(event) => setPersonalization({ layout_density: event.target.value })}
                >
                  <option value="compact">Compact</option>
                  <option value="comfortable">Comfortable</option>
                  <option value="spacious">Spacious</option>
                </select>
              </label>
              <label className="field">
                <span>Contact header Typst template</span>
                <textarea
                  className="mono"
                  value={profile.personalization.contact_header_typst}
                  onChange={(event) => setPersonalization({ contact_header_typst: event.target.value })}
                  rows={8}
                  placeholder={'Use placeholders like {{NAME}}, {{LOCATION}}, {{EMAIL}}, {{LINKEDIN}}, {{LINKEDIN_URL}}, {{GITHUB}}, {{GITHUB_URL}}. Leave empty to use the default header.'}
                />
              </label>
              <label className="field">
                <span>Fallback profile sentence</span>
                <textarea
                  value={profile.personalization.default_profile_text}
                  onChange={(event) => setPersonalization({ default_profile_text: event.target.value })}
                  rows={3}
                />
              </label>
              <label className="field">
                <span>Extra prompt notes</span>
                <textarea
                  value={profile.personalization.extra_prompt_notes}
                  onChange={(event) => setPersonalization({ extra_prompt_notes: event.target.value })}
                  rows={4}
                  placeholder="Any personal resume preferences the generator/scorer should follow."
                />
              </label>
            </div>

            {profileError ? <p className="error">{profileError}</p> : null}
            {profileStatus ? <p className="success">{profileStatus}</p> : null}
            <button disabled={isSavingProfile} type="submit">{isSavingProfile ? "Saving..." : "Save private profile"}</button>
          </form>
        </section>
      )}
    </main>
  );
}
