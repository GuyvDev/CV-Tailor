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

const telegramSetupPrompt = `I am configuring a local Docker CV tailoring app with an optional Telegram bot. Guide me step by step. Ask me for my BotFather token, my numeric Telegram user id, and whether I run Docker Compose locally or on a server. Then help me set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, and API_URL=http://api:8000 in .env, restart docker compose, verify the bot responds, and explain how to rotate the token if I accidentally expose it. Do not ask me to paste secrets into a public chat.`;

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
  const [activeView, setActiveView] = useState<"generate" | "personalize">("generate");
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

  function resetJob() {
    setJob(null);
    setError(null);
  }

  async function refreshSetup() {
    const [configResponse, profileResponse, appSettingsResponse, profilesResponse] = await Promise.all([
      fetch(`${apiBaseUrl}/api/config`),
      fetch(`${apiBaseUrl}/api/profile`),
      fetch(`${apiBaseUrl}/api/app-settings`),
      fetch(`${apiBaseUrl}/api/profiles`),
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

  const progress = generationProgress(job);
  const currentProfile = profiles.find((item) => item.profile_id === profile.profile_id);

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">CV Docker</p>
        <h1>Tailor your resume to a job description and export it as a one-page PDF.</h1>
        <p className="lede">
          The agent reads your local profile files, selects the strongest evidence,
          scores the draft, revises it once when needed, and returns the final PDF plus Typst source.
        </p>
      </section>

      <nav className="tabs" aria-label="Workspace views">
        <button className={activeView === "generate" ? "tab active" : "tab"} type="button" onClick={() => setActiveView("generate")}>Generate</button>
        <button className={activeView === "personalize" ? "tab active" : "tab"} type="button" onClick={() => setActiveView("personalize")}>Personalize</button>
      </nav>

      {activeView === "generate" ? (
        <>
          <section className="panel">
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

                {job.compile_logs.length > 0 ? (
                  <div className="logs">
                    <h3>Compile log</h3>
                    <pre>{job.compile_logs.join("\n\n")}</pre>
                  </div>
                ) : null}
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
            </label>
            <label className="field">
              <span>Skills JSON</span>
              <textarea className="mono" value={profile.skills_json} onChange={(event) => setProfile({ ...profile, skills_json: event.target.value })} rows={10} />
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
