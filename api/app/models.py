from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.review_schema import ResumeScoreReport
from app.resume_schema import ResumeDraft


class GenerateRequest(BaseModel):
    job_description: str = Field(min_length=20)
    role_focus: str | None = None
    template: str = "default"
    label: str = ""
    source_url: str = ""
    source_job_id: str = ""
    edit_instructions: str | None = None
    approved_draft: ResumeDraft | None = None


class GenerateResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "stopped"]


class JobMetadata(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "stopped"]
    created_at: datetime
    updated_at: datetime
    role_focus: str | None = None
    template: str = "default"
    stage: str | None = None
    attempts: int = 0
    page_count: int | None = None
    pdf_url: str | None = None
    typst_url: str | None = None
    fit_summary: str | None = None
    review_summary: str | None = None
    quality_score: int | None = None
    match_score: int | None = None
    score_band: str | None = None
    keywords: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    compile_logs: list[str] = Field(default_factory=list)
    error: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CompileRequest(BaseModel):
    typst_source: str


class CompileResponse(BaseModel):
    success: bool
    page_count: int | None = None
    compile_log: str = ""
    pdf_base64: str | None = None


class EditPreviewRequest(BaseModel):
    source_job_id: str = Field(min_length=1)
    edit_instructions: str = Field(min_length=1)


class EditPreviewResponse(BaseModel):
    before_draft: ResumeDraft
    after_draft: ResumeDraft


class ScoreResumeRequest(BaseModel):
    source_job_id: str = Field(min_length=1)
    job_description: str | None = Field(default=None, min_length=20)


class ScoreResumeResponse(BaseModel):
    source_job_id: str
    used_original_job_description: bool
    report: ResumeScoreReport


class ProfileSummary(BaseModel):
    profile_id: str
    display_name: str
    active: bool = False
    profile_dir: str
    exists: bool = True


class CreateProfileRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    display_name: str = ""
    copy_example: bool = True


class SwitchProfileRequest(BaseModel):
    profile_id: str = Field(min_length=1)


class PersonalizationOptions(BaseModel):
    required_profile_phrase: str = ""
    required_profile_recommendation: str = ""
    forbidden_content_regex: str = ""
    forbidden_content_gap: str = ""
    forbidden_content_recommendation: str = ""
    fixed_education_typst: str = ""
    contact_header_typst: str = ""
    layout_density: str = "compact"
    default_profile_text: str = ""
    extra_prompt_notes: str = ""


class ProfileBundle(BaseModel):
    profile_id: str = "default"
    display_name: str = "Default"
    profile_dir: str
    exists: bool
    master_profile: str = ""
    projects_json: str = "[]"
    skills_json: str = "{}"
    rules: str = ""
    research_guidelines: str = ""
    personalization: PersonalizationOptions = Field(default_factory=PersonalizationOptions)
    template_exists: bool = False


class SaveProfileBundleRequest(BaseModel):
    master_profile: str = ""
    projects_json: str = "[]"
    skills_json: str = "{}"
    rules: str = ""
    research_guidelines: str = ""
    personalization: PersonalizationOptions = Field(default_factory=PersonalizationOptions)
    initialize_templates: bool = True


class AppSettingsResponse(BaseModel):
    llm_provider: str = "openai"
    model_name: str = "gpt-5-mini"
    reasoning_effort: str = "low"
    abacus_base_url: str = "https://routellm.abacus.ai/v1"
    output_basename: str = "tailored-resume"
    enable_demo_mode: bool = True
    openai_api_key_configured: bool = False
    abacus_api_key_configured: bool = False


class SaveAppSettingsRequest(BaseModel):
    llm_provider: str = "openai"
    model_name: str = "gpt-5-mini"
    reasoning_effort: str = "low"
    abacus_base_url: str = "https://routellm.abacus.ai/v1"
    output_basename: str = "tailored-resume"
    enable_demo_mode: bool = True
    openai_api_key: str = ""
    abacus_api_key: str = ""


class AiProfileDraftRequest(BaseModel):
    source_text: str = Field(min_length=20)


class AiProfileDraftResponse(BaseModel):
    summary: str
    profile: ProfileBundle


class StatelessGenerateRequest(BaseModel):
    candidate_profile: str = Field(min_length=20)
    job_description: str = Field(min_length=20)
    projects_json: str = "[]"
    skills_json: str = "{}"
    role_focus: str | None = None
    rules: str = ""
    research_guidelines: str = ""
    personalization: PersonalizationOptions = Field(default_factory=PersonalizationOptions)
    output_basename: str = "tailored-resume"
    template_typst: str = ""


class StatelessGenerateResponse(BaseModel):
    pdf_base64: str
    typst_source: str
    page_count: int
    draft: ResumeDraft
    score_report: ResumeScoreReport | None = None
    compile_logs: list[str] = Field(default_factory=list)
    output_basename: str = "tailored-resume"
    model_name: str




class OutputCleanupRequest(BaseModel):
    older_than_days: int = Field(default=30, ge=0, le=3650)
    include_failed: bool = True
    include_stopped: bool = True
    delete_all_terminal: bool = False


class OutputCleanupResponse(BaseModel):
    output_dir: str
    total_jobs: int
    terminal_jobs: int
    active_jobs: int
    deleted_jobs: int = 0
    deleted_job_ids: list[str] = Field(default_factory=list)
    retained_jobs: int
    bytes_before: int
    bytes_after: int

class RuntimeConfigResponse(BaseModel):
    llm_provider: str
    model_name: str
    live_model_available: bool
    demo_mode_enabled: bool
    profile_dir: str
    output_dir: str
    output_basename: str
    personalized_profile_phrase_configured: bool
    personalized_forbidden_rule_configured: bool
    openai_api_key_configured: bool
    abacus_api_key_configured: bool
