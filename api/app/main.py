from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta

import httpx
from openai import OpenAI
from dataclasses import dataclass, field
from uuid import uuid4
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from app.config import get_settings
from app.cover_letter_renderer import (
    compact_cover_letter,
    cover_letter_company_edit,
    infer_cover_letter_company,
    is_header_only_cover_letter_edit,
    render_cover_letter,
)
from app.data_loader import (
    PersonalizationSettings,
    ProfileData,
    extract_contact_fields,
    load_profile_data,
)
from app.models import (
    AiProfileDraftRequest,
    AiProfileDraftResponse,
    AppSettingsResponse,
    CoverLetterEditRequest,
    CoverLetterRequest,
    CoverLetterResponse,
    CreateProfileRequest,
    EditPreviewRequest,
    EditPreviewResponse,
    GenerateRequest,
    GenerateResponse,
    JobMetadata,
    OutputCleanupRequest,
    OutputCleanupResponse,
    PersonalizationOptions,
    ProfileBundle,
    ProfileSummary,
    RuntimeConfigResponse,
    SaveAppSettingsRequest,
    SaveProfileBundleRequest,
    ScoreResumeRequest,
    SwitchProfileRequest,
    ScoreResumeResponse,
    StatelessGenerateRequest,
    StatelessGenerateResponse,
)
from app.review_schema import ResumeScoreReport
from app.renderer import render_resume
from app.resume_schema import ResumeDraft
from app.services.compiler_client import CompilerClient
from app.services.job_store import JobStore
from app.services.openai_service import ResumeGenerator
from app.services.provider_runtime import (
    CodexRunner,
    ProviderQuotaError,
    ProviderUsageStore,
)


settings = get_settings()
job_store = JobStore(settings.output_dir)
compiler_client = CompilerClient(settings.compiler_url)
provider_usage_store = ProviderUsageStore(settings.profile_dir.parent / "provider_usage.json")
codex_runner = CodexRunner(
    model=settings.codex_model_name,
    reasoning_effort=settings.codex_reasoning_effort,
    timeout_seconds=settings.codex_timeout_seconds,
    work_dir=settings.codex_work_dir,
)
TERMINAL_STATUSES = {"completed", "failed", "stopped"}


def _stateless_only() -> bool:
    return os.getenv("STATELESS_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}


def _docs_enabled() -> bool:
    return settings.enable_docs


def _public_files_enabled() -> bool:
    return settings.public_files_enabled


AUTH_EXEMPT_PREFIXES = ("/api/health",)


def _stateless_allowed_prefixes() -> tuple[str, ...]:
    prefixes = ["/api/stateless", "/api/health"]
    if _docs_enabled():
        prefixes.extend(["/docs", "/redoc", "/openapi.json"])
    return tuple(prefixes)


def _public_allowed_prefixes() -> tuple[str, ...]:
    prefixes = ["/api/health"]
    if _docs_enabled():
        prefixes.extend(["/docs", "/redoc", "/openapi.json"])
    return tuple(prefixes)


PROFILE_FILES = {
    "master_profile": "master_profile.md",
    "projects_json": "projects.json",
    "skills_json": "skills.json",
    "rules": "rules.md",
    "research_guidelines": "research_guidelines.md",
}

LEGACY_PROFILE_ID = "default"


def _profiles_root() -> Path:
    return settings.profile_dir.parent / "profiles"


def _active_profile_file() -> Path:
    return settings.profile_dir.parent / "active_profile.txt"


def _safe_profile_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip(".-_").lower()
    if not safe:
        raise HTTPException(status_code=422, detail="Profile id must contain at least one letter or number.")
    if safe in {"profile", "profile.example", "now", "outputs", "temp"}:
        raise HTTPException(status_code=422, detail="That profile id is reserved.")
    return safe[:64]


def _active_profile_id() -> str:
    path = _active_profile_file()
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return _safe_profile_id(value) if value != LEGACY_PROFILE_ID else LEGACY_PROFILE_ID
    return os.getenv("ACTIVE_PROFILE_ID", LEGACY_PROFILE_ID).strip() or LEGACY_PROFILE_ID


def _set_active_profile_id(profile_id: str) -> None:
    profile_id = _safe_profile_id(profile_id) if profile_id != LEGACY_PROFILE_ID else LEGACY_PROFILE_ID
    _active_profile_file().write_text(profile_id + "\n", encoding="utf-8")


def _profile_dir(profile_id: str | None = None) -> Path:
    profile_id = profile_id or _active_profile_id()
    if profile_id == LEGACY_PROFILE_ID:
        return settings.profile_dir
    return _profiles_root() / _safe_profile_id(profile_id)


def _profile_meta_path(profile_id: str | None = None) -> Path:
    return _profile_dir(profile_id) / "profile_meta.json"


def _profile_display_name(profile_id: str | None = None) -> str:
    profile_id = profile_id or _active_profile_id()
    path = _profile_meta_path(profile_id)
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("display_name", "")
            if value:
                return str(value)
        except Exception:
            pass
    return "Default" if profile_id == LEGACY_PROFILE_ID else profile_id.replace("-", " ").title()


def _write_profile_meta(profile_id: str, display_name: str) -> None:
    profile_dir = _profile_dir(profile_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _profile_meta_path(profile_id).write_text(
        json.dumps({"profile_id": profile_id, "display_name": display_name or _profile_display_name(profile_id)}, indent=2),
        encoding="utf-8",
    )


def _list_profile_summaries() -> list[ProfileSummary]:
    active_id = _active_profile_id()
    summaries = [
        ProfileSummary(
            profile_id=LEGACY_PROFILE_ID,
            display_name=_profile_display_name(LEGACY_PROFILE_ID),
            active=active_id == LEGACY_PROFILE_ID,
            profile_dir=str(settings.profile_dir),
            exists=settings.profile_dir.exists(),
        )
    ]
    root = _profiles_root()
    if root.exists():
        for child in sorted(path for path in root.iterdir() if path.is_dir()):
            profile_id = child.name
            summaries.append(
                ProfileSummary(
                    profile_id=profile_id,
                    display_name=_profile_display_name(profile_id),
                    active=active_id == profile_id,
                    profile_dir=str(child),
                    exists=child.exists(),
                )
            )
    return summaries


def _app_settings_path() -> Path:
    return _profile_dir() / "app_settings.json"


def _read_app_settings_secret() -> dict[str, object]:
    data: dict[str, object] = {
        "llm_provider": settings.llm_provider,
        "model_name": settings.model_name,
        "reasoning_effort": settings.reasoning_effort,
        "abacus_base_url": settings.abacus_base_url,
        "output_basename": os.getenv("OUTPUT_BASENAME", "tailored-resume"),
        "enable_demo_mode": settings.enable_demo_mode,
        "openai_api_key": settings.openai_api_key or "",
        "abacus_api_key": settings.abacus_api_key or "",
    }
    path = _app_settings_path()
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        data.update({key: value for key, value in saved.items() if value is not None})
    return data


def _app_settings_response() -> AppSettingsResponse:
    data = _read_app_settings_secret()
    return AppSettingsResponse(
        llm_provider=str(data.get("llm_provider") or "auto"),
        model_name=str(data.get("model_name") or "gpt-5-mini"),
        reasoning_effort=str(data.get("reasoning_effort") or "low"),
        abacus_base_url=str(data.get("abacus_base_url") or "https://routellm.abacus.ai/v1"),
        output_basename=str(data.get("output_basename") or "tailored-resume"),
        enable_demo_mode=bool(data.get("enable_demo_mode", True)),
        openai_api_key_configured=bool(str(data.get("openai_api_key") or "").strip()),
        abacus_api_key_configured=bool(str(data.get("abacus_api_key") or "").strip()),
        codex_installed=codex_runner.installed(),
        codex_authenticated=codex_runner.auth_file_exists(),
    )


def _write_app_settings(payload: SaveAppSettingsRequest) -> AppSettingsResponse:
    _profile_dir().mkdir(parents=True, exist_ok=True)
    current = _read_app_settings_secret()
    next_data = {
        "llm_provider": payload.llm_provider.strip().lower() or "auto",
        "model_name": payload.model_name.strip() or "gpt-5-mini",
        "reasoning_effort": payload.reasoning_effort.strip() or "low",
        "abacus_base_url": payload.abacus_base_url.strip().rstrip("/") or "https://routellm.abacus.ai/v1",
        "output_basename": payload.output_basename.strip() or "tailored-resume",
        "enable_demo_mode": payload.enable_demo_mode,
        "openai_api_key": payload.openai_api_key.strip() or str(current.get("openai_api_key") or ""),
        "abacus_api_key": payload.abacus_api_key.strip() or str(current.get("abacus_api_key") or ""),
    }
    _app_settings_path().write_text(json.dumps(next_data, indent=2), encoding="utf-8")
    return _app_settings_response()


def _resume_generator(provider_override: str | None = None) -> ResumeGenerator:
    data = _read_app_settings_secret()
    provider = provider_override or str(data.get("llm_provider") or "auto").strip().lower()
    if provider == "auto":
        provider = (
            "codex"
            if codex_runner.installed() and codex_runner.auth_file_exists()
            else "abacus"
            if str(data.get("abacus_api_key") or "").strip()
            else "codex"
        )
    return ResumeGenerator(
        provider=provider,
        api_key=str(data.get("openai_api_key") or "") or None,
        abacus_api_key=str(data.get("abacus_api_key") or "") or None,
        abacus_base_url=str(data.get("abacus_base_url") or "https://routellm.abacus.ai/v1"),
        model_name=str(data.get("model_name") or "gpt-5-mini"),
        reasoning_effort=str(data.get("reasoning_effort") or "low"),
        enable_demo_mode=bool(data.get("enable_demo_mode", True)),
        codex_runner=codex_runner,
        usage_store=provider_usage_store,
    )


def _provider_candidates(requested: str | None, source_job_id: str = "") -> list[str]:
    if source_job_id:
        try:
            source = job_store.load(source_job_id)
            pinned = str(source.extra.get("actual_provider") or "").strip().lower()
            if pinned in {"codex", "abacus", "openai"}:
                return [pinned]
        except FileNotFoundError:
            pass

    data = _read_app_settings_secret()
    selected = (requested or str(data.get("llm_provider") or "auto")).strip().lower()
    if selected != "auto":
        return [selected]

    codex_status = codex_runner.account_status()
    abacus_configured = bool(str(data.get("abacus_api_key") or "").strip())
    availability = {
        "codex": bool(codex_status.get("available")),
        "abacus": abacus_configured and not provider_usage_store.abacus_quota_blocked(),
    }
    order = settings.auto_provider_order or ("codex", "abacus")
    available = [provider for provider in order if availability.get(provider)]
    # Keep configured fallbacks in the list: a stale preflight should not prevent
    # a real attempt, and quota exceptions will advance to the next provider.
    configured = {
        "codex": codex_runner.installed() and codex_runner.auth_file_exists(),
        "abacus": abacus_configured,
    }
    available.extend(
        provider
        for provider in order
        if configured.get(provider) and provider not in available
    )
    return available


def _generator_for_source(source_job_id: str) -> ResumeGenerator:
    candidates = _provider_candidates(None, source_job_id)
    if not candidates:
        raise RuntimeError("Neither Codex nor Abacus is configured and available.")
    return _resume_generator(candidates[0])


def _output_basename() -> str:
    return _safe_part(str(_read_app_settings_secret().get("output_basename") or "tailored-resume"), max_len=80)


def _default_stateless_template() -> str:
    return '#set page(\n  paper: "us-letter",\n  margin: (x: 2.54cm, y: 2.00cm),\n)\n\n#set text(font: "DejaVu Sans", size: 10pt, fill: black)\n#set par(leading: 0.5em, justify: true, spacing: 0pt)\n#set block(spacing: 6pt)\n\n#let theme-blue = rgb("#00508C")\n\n#let section(title) = {\n  stack(\n    dir: ttb,\n    spacing: 1pt,\n    text(fill: theme-blue, weight: "bold", size: 11pt)[#upper(title)],\n    line(length: 100%, stroke: 0.5pt + theme-blue),\n  )\n  v(4pt)\n}\n\n#let project(title) = {\n  v(4pt)\n  text(fill: theme-blue, weight: "bold")[#title]\n}\n\n#let bullet(content) = {\n  grid(\n    columns: (12pt, 1fr),\n    gutter: 0pt,\n    align: (right, left),\n    [•#h(4pt)],\n    content\n  )\n}\n\n// Header\n{{CONTACT_HEADER}}\n\n#v(8pt)\n\n{{PROFILE_SECTION}}\n\n{{BODY_CONTENT}}\n'


def _stateless_template(payload: StatelessGenerateRequest) -> str:
    if payload.template_typst.strip():
        return payload.template_typst
    template_path = settings.profile_dir.parent / "profile.example" / "templates" / "base_resume.typ"
    if not template_path.exists():
        template_path = Path(__file__).parents[2] / "data" / "profile.example" / "templates" / "base_resume.typ"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return _default_stateless_template()


def _stateless_profile(payload: StatelessGenerateRequest) -> ProfileData:
    try:
        projects = json.loads(payload.projects_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"projects_json is invalid JSON: {exc.msg}") from exc
    try:
        skills = json.loads(payload.skills_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"skills_json is invalid JSON: {exc.msg}") from exc
    if not isinstance(projects, list):
        raise HTTPException(status_code=422, detail="projects_json must be a JSON array.")
    if not isinstance(skills, dict):
        raise HTTPException(status_code=422, detail="skills_json must be a JSON object.")

    return ProfileData(
        master_profile=payload.candidate_profile,
        projects=[project for project in projects if str(project.get("cv_status", "active")).lower() not in {"hold", "on_hold", "paused"}],
        skills={str(key): [str(item) for item in value] for key, value in skills.items() if isinstance(value, list)},
        rules=payload.rules,
        cv_guidance=payload.research_guidelines,
        template=_stateless_template(payload),
        examples={},
        style_references=[],
        personalization=PersonalizationSettings.from_dict(payload.personalization.model_dump()),
    )


def _stateless_generator() -> ResumeGenerator:
    flash_key = os.getenv("FLASH_API_KEY", "").strip()
    provider = os.getenv("STATELESS_LLM_PROVIDER", "gemini").strip().lower()
    model_name = os.getenv("STATELESS_MODEL_NAME", "gemini-2.5-flash").strip()
    base_url = os.getenv("STATELESS_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
    enable_demo = os.getenv("STATELESS_ENABLE_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if provider == "gemini":
        return ResumeGenerator(
            provider="abacus",
            api_key=None,
            abacus_api_key=flash_key or None,
            abacus_base_url=base_url,
            model_name=model_name,
            reasoning_effort=os.getenv("STATELESS_REASONING_EFFORT", "medium"),
            enable_demo_mode=enable_demo,
        )
    return ResumeGenerator(
        provider=provider,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        abacus_api_key=os.getenv("ABACUS_API_KEY") or None,
        abacus_base_url=os.getenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1").rstrip("/"),
        model_name=model_name,
        reasoning_effort=os.getenv("STATELESS_REASONING_EFFORT", settings.reasoning_effort),
        enable_demo_mode=enable_demo,
    )


def _run_stateless_generation(payload: StatelessGenerateRequest) -> StatelessGenerateResponse:
    profile = _stateless_profile(payload)
    generator = _stateless_generator()
    request = GenerateRequest(
        job_description=payload.job_description,
        role_focus=payload.role_focus,
        template="stateless",
        label="stateless",
    )
    attempts = max(1, min(settings.max_generator_retries, 3))
    scorer_attempts = max(1, min(settings.max_scorer_retries, attempts))
    previous_draft: ResumeDraft | None = None
    scorer_feedback: ResumeScoreReport | None = None
    compile_feedback: str | None = None
    compile_logs: list[str] = []
    last_source = ""
    last_pdf = ""
    last_page_count = 0
    last_draft: ResumeDraft | None = None

    for attempt in range(1, attempts + 1):
        draft = generator.generate_resume(
            profile,
            request,
            attempt,
            previous_draft,
            scorer_feedback,
            compile_feedback,
        )
        last_draft = draft
        last_source = render_resume(profile.template, draft, profile.personalization)
        compile_result = asyncio.run(compiler_client.compile(last_source))
        compile_feedback = (
            f"success={compile_result.success}, "
            f"page_count={compile_result.page_count}, "
            f"log={compile_result.compile_log.strip() or 'n/a'}"
        )
        compile_logs.append(f"gen {attempt}: {compile_feedback}")
        last_page_count = compile_result.page_count or 0
        last_pdf = compile_result.pdf_base64 or ""
        if not compile_result.success or not last_pdf:
            raise HTTPException(status_code=502, detail=f"Typst compilation failed: {compile_result.compile_log[:800]}")

        if attempt <= scorer_attempts:
            scorer_feedback = generator.score_resume(
                profile,
                request,
                draft,
                attempt,
                attempt,
                compile_feedback,
            )
            compile_logs.append(
                f"scr {attempt}: quality={scorer_feedback.quality_score}, "
                f"match={scorer_feedback.match_score}, decision={scorer_feedback.decision}"
            )
            if scorer_feedback.decision == "approve" and last_page_count == 1:
                break
        previous_draft = draft

    if last_draft is None or not last_pdf:
        raise HTTPException(status_code=500, detail="Stateless generation produced no output.")
    return StatelessGenerateResponse(
        pdf_base64=last_pdf,
        typst_source=last_source,
        page_count=last_page_count,
        draft=last_draft,
        score_report=scorer_feedback,
        compile_logs=compile_logs,
        output_basename=_safe_part(payload.output_basename or "tailored-resume", max_len=80),
        model_name=generator.model_name,
    )


def _profile_draft_schema() -> dict:
    return {
        "name": "profile_personalization_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "master_profile": {"type": "string"},
                "projects": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "skills": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
                "rules": {"type": "string"},
                "research_guidelines": {"type": "string"},
                "personalization": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "required_profile_phrase": {"type": "string"},
                        "required_profile_recommendation": {"type": "string"},
                        "forbidden_content_regex": {"type": "string"},
                        "forbidden_content_gap": {"type": "string"},
                        "forbidden_content_recommendation": {"type": "string"},
                        "fixed_education_typst": {"type": "string"},
                        "contact_header_typst": {"type": "string"},
                        "layout_density": {"type": "string"},
                        "default_profile_text": {"type": "string"},
                        "extra_prompt_notes": {"type": "string"},
                    },
                    "required": [
                        "required_profile_phrase",
                        "required_profile_recommendation",
                        "forbidden_content_regex",
                        "forbidden_content_gap",
                        "forbidden_content_recommendation",
                        "fixed_education_typst",
                        "contact_header_typst",
                        "layout_density",
                        "default_profile_text",
                        "extra_prompt_notes",
                    ],
                },
            },
            "required": [
                "summary",
                "master_profile",
                "projects",
                "skills",
                "rules",
                "research_guidelines",
                "personalization",
            ],
        },
    }


def _build_ai_profile_prompt(source_text: str) -> tuple[str, str]:
    system_prompt = """You convert messy candidate notes into private local profile files for a resume-tailoring app.
Return only verified facts from the user's text. Do not invent employers, dates, metrics, awards, tools, degrees, or links.
You must extract useful content when the user's text contains contact, education, projects, skills, honors, or writing preferences.
Make the output easy for a human to review before saving. Return one JSON object only.
Projects must be an array of objects with title, dates, stack, cv_status, and facts.
Skills must be an object mapping categories to arrays of strings.
Personalization should contain writing preferences and rendering rules only when clearly implied by the user.
Use empty strings only for truly unknown personalization fields, not for the main profile when facts are provided.
For forbidden_content_regex, write a valid Python regular expression only when the user explicitly names content that should be removed or not generated."""
    user_prompt = f"""Create draft profile files from this user input.

Return JSON with exactly this shape:
{{
  "summary": "one sentence explaining what was extracted",
  "master_profile": "# Candidate Profile\n\n## Contact\n* Name: ...\n* Email: ...\n* Location: ...\n* LinkedIn: ...\n* GitHub: ...\n\n## Summary Facts\n* ...\n\n## Education\n* School\n* Degree\n* Dates\n* Coursework or honors",
  "projects": [
    {{"title": "Project name", "dates": "YYYY", "stack": ["Tool"], "cv_status": "active", "facts": ["Verified fact bullet"]}}
  ],
  "skills": {{"languages": ["Python"], "tools": ["Docker"]}},
  "rules": "resume writing rules derived from the user's preferences",
  "research_guidelines": "review/scoring guidance",
  "personalization": {{
    "required_profile_phrase": "",
    "required_profile_recommendation": "",
    "forbidden_content_regex": "",
    "forbidden_content_gap": "",
    "forbidden_content_recommendation": "",
    "fixed_education_typst": "",
    "contact_header_typst": "",
    "layout_density": "compact",
    "default_profile_text": "",
    "extra_prompt_notes": ""
  }}
}}

User input:
{source_text}
"""
    return system_prompt, user_prompt


def _profile_bundle_from_ai_payload(payload: dict) -> AiProfileDraftResponse:
    profile = ProfileBundle(
        profile_id=_active_profile_id(),
        display_name=_profile_display_name(),
        profile_dir=str(_profile_dir()),
        exists=_profile_dir().exists(),
        master_profile=str(payload.get("master_profile", "")),
        projects_json=json.dumps(payload.get("projects", []), indent=2, ensure_ascii=False),
        skills_json=json.dumps(payload.get("skills", {}), indent=2, ensure_ascii=False),
        rules=str(payload.get("rules", "")),
        research_guidelines=str(payload.get("research_guidelines", "")),
        personalization=PersonalizationOptions.model_validate(payload.get("personalization", {})),
        template_exists=(_profile_dir() / "templates" / "base_resume.typ").exists(),
    )
    return AiProfileDraftResponse(summary=str(payload.get("summary", "Draft created.")), profile=profile)


def _validate_ai_profile_payload(payload: dict) -> None:
    master_profile = str(payload.get("master_profile", "")).strip()
    projects = payload.get("projects", [])
    skills = payload.get("skills", {})
    if len(master_profile) < 200:
        raise HTTPException(status_code=502, detail="AI setup draft was too sparse; add more candidate notes or try again.")
    if not isinstance(projects, list) or not projects:
        raise HTTPException(status_code=502, detail="AI setup draft did not extract any projects.")
    if not isinstance(skills, dict) or not any(skills.values()):
        raise HTTPException(status_code=502, detail="AI setup draft did not extract any skills.")


def _draft_profile_with_ai(source_text: str) -> AiProfileDraftResponse:
    data = _read_app_settings_secret()
    provider = str(data.get("llm_provider") or "auto").strip().lower()
    if provider == "auto":
        candidates = _provider_candidates("auto")
        if not candidates:
            raise HTTPException(status_code=409, detail="Neither Codex nor Abacus is available.")
        provider = candidates[0]
    model = str(data.get("model_name") or "gpt-5-mini")
    system_prompt, user_prompt = _build_ai_profile_prompt(source_text)
    schema = _profile_draft_schema()

    if provider == "codex":
        decoded = codex_runner.generate(
            f"Role and constraints:\n{system_prompt}\n\nTask input:\n{user_prompt}",
            schema["schema"],
        )
        _validate_ai_profile_payload(decoded)
        return _profile_bundle_from_ai_payload(decoded)

    if provider == "abacus":
        api_key = str(data.get("abacus_api_key") or "")
        if not api_key:
            raise HTTPException(status_code=409, detail="Add an Abacus API key in App settings first.")
        response = httpx.post(
            f"{str(data.get('abacus_base_url') or 'https://routellm.abacus.ai/v1').rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)
        _validate_ai_profile_payload(payload)
        return _profile_bundle_from_ai_payload(payload)

    api_key = str(data.get("openai_api_key") or "")
    if not api_key:
        raise HTTPException(status_code=409, detail="Add an OpenAI API key in App settings first.")
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text={"format": {"type": "json_schema", **schema}},
    )
    payload = response.output_text
    if not payload:
        raise HTTPException(status_code=502, detail="AI returned an empty profile draft.")
    decoded = json.loads(payload)
    _validate_ai_profile_payload(decoded)
    return _profile_bundle_from_ai_payload(decoded)


def _read_profile_file(filename: str, default: str = "") -> str:
    path = _profile_dir() / filename
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def _example_profile_dir() -> Path:
    return settings.profile_dir.parent / "profile.example"


def _initialize_profile_templates() -> None:
    source_templates = _example_profile_dir() / "templates"
    target_templates = _profile_dir() / "templates"
    if not source_templates.exists() or target_templates.exists():
        return
    shutil.copytree(source_templates, target_templates)



def _read_personalization() -> PersonalizationOptions:
    path = _profile_dir() / "personalization.json"
    if not path.exists():
        return PersonalizationOptions()
    return PersonalizationOptions.model_validate_json(path.read_text(encoding="utf-8"))


def _write_personalization(personalization: PersonalizationOptions) -> None:
    (_profile_dir() / "personalization.json").write_text(
        personalization.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _profile_bundle() -> ProfileBundle:
    return ProfileBundle(
        profile_id=_active_profile_id(),
        display_name=_profile_display_name(),
        profile_dir=str(_profile_dir()),
        exists=_profile_dir().exists(),
        master_profile=_read_profile_file("master_profile.md"),
        projects_json=_read_profile_file("projects.json", "[]"),
        skills_json=_read_profile_file("skills.json", "{}"),
        rules=_read_profile_file("rules.md"),
        research_guidelines=_read_profile_file("research_guidelines.md"),
        personalization=_read_personalization(),
        template_exists=(_profile_dir() / "templates" / "base_resume.typ").exists(),
    )

APPROVED_EDIT_ONE_PAGE_INSTRUCTIONS = (
    "Make this approved edit fit on exactly one page. Keep the same role target and "
    "the same verified facts. Shorten the profile, compress wording, trim weaker bullets, "
    "and remove low-value redundancy without adding new facts."
)


@dataclass
class JobControl:
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


job_controls: dict[str, JobControl] = {}

app = FastAPI(
    title="cv-docker-api",
    version="0.1.0",
    docs_url="/docs" if _docs_enabled() else None,
    redoc_url="/redoc" if _docs_enabled() else None,
    openapi_url="/openapi.json" if _docs_enabled() else None,
)
if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )


def _authorized(request: Request) -> bool:
    token = settings.api_auth_token or ""
    if not token:
        return False
    supplied = request.headers.get("authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = request.headers.get("x-api-token", "").strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


@app.middleware("http")
async def production_guard(request: Request, call_next):
    path = request.url.path
    if _stateless_only() and path.startswith("/api/") and not path.startswith(_stateless_allowed_prefixes()):
        return JSONResponse({"detail": "This deployment is running in stateless-only mode."}, status_code=404)
    protected_path = path.startswith("/api/") or path.startswith("/files/")
    exempt_path = path.startswith(AUTH_EXEMPT_PREFIXES)
    if not _stateless_only() and settings.require_api_auth and protected_path and not exempt_path and not _authorized(request):
        return JSONResponse({"detail": "Authentication required."}, status_code=status.HTTP_401_UNAUTHORIZED)
    return await call_next(request)


if not _stateless_only() and _public_files_enabled():
    app.mount("/files", StaticFiles(directory=settings.output_dir), name="files")


@app.get("/api/config", response_model=RuntimeConfigResponse)
async def runtime_config() -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        llm_provider=_app_settings_response().llm_provider,
        model_name=_app_settings_response().model_name,
        live_model_available=_resume_generator().has_live_model(),
        demo_mode_enabled=_app_settings_response().enable_demo_mode,
        profile_dir=str(_profile_dir()),
        output_dir=str(settings.output_dir),
        output_basename=_app_settings_response().output_basename,
        personalized_profile_phrase_configured=bool(_read_personalization().required_profile_phrase.strip()),
        personalized_forbidden_rule_configured=bool(_read_personalization().forbidden_content_regex.strip()),
        openai_api_key_configured=_app_settings_response().openai_api_key_configured,
        abacus_api_key_configured=_app_settings_response().abacus_api_key_configured,
    )


@app.get("/api/app-settings", response_model=AppSettingsResponse)
async def get_app_settings() -> AppSettingsResponse:
    return _app_settings_response()


@app.get("/api/provider-usage")
async def provider_usage() -> dict[str, object]:
    data = _read_app_settings_secret()
    codex_status = await asyncio.to_thread(codex_runner.account_status, force=True)
    abacus_usage = provider_usage_store.read()["abacus"]
    return {
        "default_provider": str(data.get("llm_provider") or "auto"),
        "auto_provider_order": list(settings.auto_provider_order or ("codex", "abacus")),
        "codex": codex_status,
        "abacus": {
            "configured": bool(str(data.get("abacus_api_key") or "").strip()),
            "available": bool(str(data.get("abacus_api_key") or "").strip())
            and not provider_usage_store.abacus_quota_blocked(),
            "balance_supported": False,
            "usage": abacus_usage,
        },
    }


@app.post("/api/app-settings", response_model=AppSettingsResponse)
async def save_app_settings(payload: SaveAppSettingsRequest) -> AppSettingsResponse:
    return _write_app_settings(payload)


@app.get("/api/profiles", response_model=list[ProfileSummary])
async def list_profiles() -> list[ProfileSummary]:
    return _list_profile_summaries()


@app.post("/api/profiles", response_model=ProfileSummary)
async def create_profile(payload: CreateProfileRequest) -> ProfileSummary:
    profile_id = _safe_profile_id(payload.profile_id)
    profile_dir = _profile_dir(profile_id)
    if profile_dir.exists() and any(profile_dir.iterdir()):
        raise HTTPException(status_code=409, detail="Profile already exists.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    if payload.copy_example:
        example_dir = _example_profile_dir()
        if example_dir.exists():
            for item in example_dir.iterdir():
                target = profile_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
    _write_profile_meta(profile_id, payload.display_name or profile_id.replace("-", " ").title())
    _set_active_profile_id(profile_id)
    return next(summary for summary in _list_profile_summaries() if summary.profile_id == profile_id)


@app.post("/api/profiles/active", response_model=ProfileSummary)
async def switch_profile(payload: SwitchProfileRequest) -> ProfileSummary:
    profile_id = payload.profile_id if payload.profile_id == LEGACY_PROFILE_ID else _safe_profile_id(payload.profile_id)
    if not _profile_dir(profile_id).exists():
        raise HTTPException(status_code=404, detail="Profile not found.")
    _set_active_profile_id(profile_id)
    return next(summary for summary in _list_profile_summaries() if summary.profile_id == profile_id)


@app.delete("/api/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: str) -> None:
    if profile_id == LEGACY_PROFILE_ID:
        raise HTTPException(status_code=409, detail="The default profile cannot be deleted.")
    profile_id = _safe_profile_id(profile_id)
    if profile_id == _active_profile_id():
        raise HTTPException(status_code=409, detail="Switch to another profile before deleting this one.")

    profile_dir = _profile_dir(profile_id)
    if not profile_dir.exists():
        raise HTTPException(status_code=404, detail="Profile not found.")

    root = _profiles_root().resolve()
    resolved = profile_dir.resolve()
    if resolved.parent != root:
        raise HTTPException(status_code=400, detail="Invalid profile path.")
    shutil.rmtree(resolved)


@app.get("/api/profile", response_model=ProfileBundle)
async def get_profile() -> ProfileBundle:
    return _profile_bundle()


@app.post("/api/profile/ai-draft", response_model=AiProfileDraftResponse)
async def draft_profile_with_ai(payload: AiProfileDraftRequest) -> AiProfileDraftResponse:
    try:
        return await asyncio.to_thread(_draft_profile_with_ai, payload.source_text)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"AI provider rejected the setup draft request: {exc.response.text[:400]}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI returned invalid JSON for the profile draft.") from exc


@app.post("/api/profile", response_model=ProfileBundle)
async def save_profile(payload: SaveProfileBundleRequest) -> ProfileBundle:
    try:
        json.loads(payload.projects_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"projects_json is invalid JSON: {exc.msg}") from exc
    try:
        json.loads(payload.skills_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"skills_json is invalid JSON: {exc.msg}") from exc
    if payload.personalization.forbidden_content_regex.strip():
        try:
            re.compile(payload.personalization.forbidden_content_regex)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"forbidden_content_regex is invalid: {exc}") from exc

    _profile_dir().mkdir(parents=True, exist_ok=True)
    (_profile_dir() / "examples").mkdir(exist_ok=True)
    (_profile_dir() / "notes").mkdir(exist_ok=True)
    if payload.initialize_templates:
        _initialize_profile_templates()

    values = {
        "master_profile.md": payload.master_profile,
        "projects.json": payload.projects_json,
        "skills.json": payload.skills_json,
        "rules.md": payload.rules,
        "research_guidelines.md": payload.research_guidelines,
    }
    for filename, content in values.items():
        (_profile_dir() / filename).write_text(content.rstrip() + "\n", encoding="utf-8")
    _write_personalization(payload.personalization)
    return _profile_bundle()


@app.get("/api/health")
async def healthcheck() -> dict[str, str]:
    generator = _stateless_generator() if _stateless_only() else _resume_generator()
    mode = "live" if generator.has_live_model() else "demo"
    return {"status": "ok", "mode": mode}


@app.post("/api/stateless/generate", response_model=StatelessGenerateResponse)
async def stateless_generate(payload: StatelessGenerateRequest) -> StatelessGenerateResponse:
    return await asyncio.to_thread(_run_stateless_generation, payload)


@app.post("/api/generate", response_model=GenerateResponse, status_code=202)
async def generate_resume(
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> GenerateResponse:
    job_id = uuid4().hex[:12]
    job_store.create_job(
        job_id,
        payload.role_focus,
        payload.template,
        extra={
            "label": payload.label,
            "source_url": payload.source_url,
            "job_description": payload.job_description,
            "requested_provider": payload.provider
            or str(_read_app_settings_secret().get("llm_provider") or "auto"),
        },
    )
    _job_control(job_id)
    background_tasks.add_task(run_generation_job, job_id, payload)
    return GenerateResponse(job_id=job_id, status="queued")


@app.post("/api/edit-preview", response_model=EditPreviewResponse)
async def edit_preview(payload: EditPreviewRequest) -> EditPreviewResponse:
    try:
        source_job = job_store.load(payload.source_job_id)
        previous_draft = _load_stored_draft(payload.source_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Source CV draft not found") from exc

    job_description = source_job.extra.get("job_description", "")
    if not job_description:
        raise HTTPException(status_code=409, detail="Original job description is missing")

    profile = await asyncio.to_thread(load_profile_data, _profile_dir())
    request = GenerateRequest(
        job_description=job_description,
        role_focus=source_job.role_focus,
        template=source_job.template,
        label=source_job.extra.get("label", ""),
        source_url=source_job.extra.get("source_url", ""),
        source_job_id=payload.source_job_id,
        edit_instructions=payload.edit_instructions,
    )
    edited_draft = await asyncio.to_thread(
        _generator_for_source(payload.source_job_id).generate_resume,
        profile,
        request,
        1,
        previous_draft,
    )
    return EditPreviewResponse(before_draft=previous_draft, after_draft=edited_draft)


@app.post("/api/score-existing", response_model=ScoreResumeResponse)
async def score_existing_resume(payload: ScoreResumeRequest) -> ScoreResumeResponse:
    try:
        source_job = job_store.load(payload.source_job_id)
        draft = _load_stored_draft(payload.source_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Source CV draft not found") from exc

    job_description = payload.job_description or source_job.extra.get("job_description", "")
    if not job_description:
        raise HTTPException(status_code=409, detail="Original job description is missing")

    profile = await asyncio.to_thread(load_profile_data, _profile_dir())
    request = GenerateRequest(
        job_description=job_description,
        role_focus=source_job.role_focus,
        template=source_job.template,
        label=source_job.extra.get("label", ""),
        source_url=source_job.extra.get("source_url", ""),
        source_job_id=payload.source_job_id,
    )
    compile_feedback = (
        f"success=True, page_count={source_job.page_count}, log=n/a"
        if source_job.page_count is not None
        else None
    )
    report = await asyncio.to_thread(
        _generator_for_source(payload.source_job_id).score_resume,
        profile,
        request,
        draft,
        1,
        1,
        compile_feedback,
    )
    return ScoreResumeResponse(
        source_job_id=payload.source_job_id,
        used_original_job_description=payload.job_description is None,
        report=report,
    )


@app.post("/api/cover-letter", response_model=CoverLetterResponse)
async def generate_cover_letter(payload: CoverLetterRequest) -> CoverLetterResponse:
    try:
        source_job = job_store.load(payload.source_job_id)
        draft = _load_stored_draft(payload.source_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Completed source CV not found") from exc
    if source_job.status != "completed":
        raise HTTPException(status_code=409, detail="Cover letters require a completed CV")
    job_description = str(source_job.extra.get("job_description", "")).strip()
    if not job_description:
        raise HTTPException(status_code=409, detail="Original job description is missing")

    profile_dir = _profile_dir()
    profile = await asyncio.to_thread(load_profile_data, profile_dir)
    content = await asyncio.to_thread(
        _generator_for_source(payload.source_job_id).generate_cover_letter,
        profile,
        job_description,
        draft,
        payload.instructions,
    )
    return await _finalize_cover_letter(
        source_job,
        draft,
        profile,
        profile_dir,
        job_description,
        content,
        company_override=cover_letter_company_edit(payload.instructions),
    )


@app.post("/api/cover-letter/edit", response_model=CoverLetterResponse)
async def edit_cover_letter(payload: CoverLetterEditRequest) -> CoverLetterResponse:
    try:
        source_job = job_store.load(payload.source_job_id)
        draft = _load_stored_draft(payload.source_job_id)
        previous_path = settings.output_dir / payload.source_job_id / "cover-letter.txt"
        previous_content = previous_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Generate a cover letter for this job first") from exc
    if source_job.status != "completed":
        raise HTTPException(status_code=409, detail="Cover-letter edits require a completed CV")
    job_description = str(source_job.extra.get("job_description", "")).strip()
    if not job_description:
        raise HTTPException(status_code=409, detail="Original job description is missing")

    profile_dir = _profile_dir()
    profile = await asyncio.to_thread(load_profile_data, profile_dir)
    company_override = cover_letter_company_edit(payload.instructions)
    if is_header_only_cover_letter_edit(payload.instructions, company_override):
        content = previous_content
    else:
        content = await asyncio.to_thread(
            _generator_for_source(payload.source_job_id).generate_cover_letter,
            profile,
            job_description,
            draft,
            payload.instructions,
            previous_content,
        )
    return await _finalize_cover_letter(
        source_job,
        draft,
        profile,
        profile_dir,
        job_description,
        content,
        preserve_previous=True,
        company_override=company_override,
    )


async def _finalize_cover_letter(
    source_job: JobMetadata,
    draft: ResumeDraft,
    profile: ProfileData,
    profile_dir: Path,
    job_description: str,
    content: str,
    *,
    preserve_previous: bool = False,
    company_override: str | None = None,
) -> CoverLetterResponse:
    template_path = profile_dir / "templates" / "cover_letter.typ"
    if not template_path.exists():
        template_path = _example_profile_dir() / "templates" / "cover_letter.typ"
    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Cover-letter Typst template is missing")

    contact = extract_contact_fields(profile.master_profile)
    contact.update(
        {
            "name": draft.contact.name or contact.get("name", "Applicant"),
            "email": draft.contact.email or contact.get("email", ""),
            "location": draft.contact.location or contact.get("location", ""),
        }
    )
    portrait_path = profile_dir / "assets" / "cover-letter-portrait-circle.png"
    if not portrait_path.exists():
        portrait_path = profile_dir / "assets" / "cover-letter-portrait.jpg"
    assets: dict[str, bytes] = {}
    portrait_filename: str | None = None
    if portrait_path.exists():
        portrait_filename = portrait_path.name
        assets[portrait_filename] = portrait_path.read_bytes()
    label = str(source_job.extra.get("label", "")).strip()
    company = company_override or _cover_letter_company(
        job_description, source_job.extra.get("source_url", ""), label
    )
    template = template_path.read_text(encoding="utf-8-sig")
    required_template_markers = {
        "{{BODY}}",
        "{{BODY_FONT_SIZE}}",
        "{{SIGNATURE_NAME}}",
        "measure(letter-body)",
        "cover letter body exceeds signature safety area",
    }
    missing_markers = sorted(
        marker for marker in required_template_markers if marker not in template
    )
    if missing_markers:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cover-letter template is missing required layout-verification markers: "
                + ", ".join(missing_markers)
            ),
        )
    compiled = None
    typst_source = ""
    body_font_size = 0.0
    last_error = ""
    content_candidates = [content]
    compacted_content = compact_cover_letter(content)
    if compacted_content != content:
        content_candidates.append(compacted_content)
    for candidate_content in content_candidates:
        content = candidate_content
        for body_font_size, paragraph_gap in ((9.7, 3.2), (9.2, 2.8), (8.8, 2.4)):
            typst_source = render_cover_letter(
                template,
                content,
                contact,
                portrait_filename=portrait_filename,
                company=company,
                recipient="Hiring Manager",
                body_font_size=body_font_size,
                paragraph_gap_mm=paragraph_gap,
            )
            compiled = await compiler_client.compile(typst_source, assets)
            last_error = compiled.compile_log
            if compiled.success and compiled.pdf_base64 and compiled.page_count == 1:
                break
            if "cover letter body exceeds signature safety area" not in last_error:
                break
        if compiled and compiled.success and compiled.pdf_base64 and compiled.page_count == 1:
            break
        if "cover letter body exceeds signature safety area" not in last_error:
            break
    if compiled is None or not compiled.success or not compiled.pdf_base64:
        raise HTTPException(
            status_code=502,
            detail=f"Cover-letter layout verification failed: {last_error[:500]}",
        )
    if compiled.page_count != 1:
        raise HTTPException(status_code=502, detail="Cover letter did not compile to exactly one page")

    filename = f"cover-letter-{_safe_part(draft.job_title or label or 'job', 45)}.pdf"
    pdf_bytes = base64.b64decode(compiled.pdf_base64)
    if preserve_previous:
        artifact_dir = settings.output_dir / source_job.job_id
        for artifact_name in ("cover-letter.txt", "cover-letter.typ", "cover-letter.pdf"):
            artifact_path = artifact_dir / artifact_name
            if artifact_path.exists():
                shutil.copyfile(artifact_path, artifact_dir / artifact_name.replace("cover-letter", "cover-letter.previous"))
    job_store.write_text_artifact(source_job.job_id, "cover-letter.txt", content)
    job_store.write_text_artifact(source_job.job_id, "cover-letter.typ", typst_source)
    job_store.write_binary_artifact(source_job.job_id, "cover-letter.pdf", pdf_bytes)
    return CoverLetterResponse(
        source_job_id=source_job.job_id,
        filename=filename,
        content=content,
        pdf_base64=compiled.pdf_base64,
        page_count=compiled.page_count,
        one_page_verified=True,
        non_overlap_verified=True,
        body_font_size=body_font_size,
    )


def _cover_letter_company(job_description: str, source_url: str, label: str) -> str:
    return infer_cover_letter_company(job_description, source_url, label)


def _safe_part(s: str, max_len: int = 30) -> str:
    """Sanitize a string for use in a filename."""
    return re.sub(r"[^\w\-]", "_", s.strip())[:max_len].strip("_") or "unknown"


def _normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _job_control(job_id: str) -> JobControl:
    control = job_controls.get(job_id)
    if control is None:
        control = JobControl()
        job_controls[job_id] = control
    return control


def _stop_metadata(
    metadata: JobMetadata,
    *,
    error: str = "Stopped by user.",
    page_count: int | None = None,
    compile_logs: list[str] | None = None,
    pdf_url: str | None = None,
    typst_url: str | None = None,
) -> JobMetadata:
    return job_store.record_status(
        metadata,
        status="stopped",
        stage="stopped",
        page_count=page_count if page_count is not None else metadata.page_count,
        compile_logs=compile_logs if compile_logs is not None else metadata.compile_logs,
        pdf_url=pdf_url if pdf_url is not None else metadata.pdf_url,
        typst_url=typst_url if typst_url is not None else metadata.typst_url,
        error=error,
    )


def _load_stored_draft(job_id: str) -> ResumeDraft:
    draft_path = settings.output_dir / job_id / "draft.json"
    if not draft_path.exists():
        raise FileNotFoundError(f"Draft for job '{job_id}' not found")
    return ResumeDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _load_job_metadata() -> list[JobMetadata]:
    jobs: list[JobMetadata] = []
    if not settings.output_dir.exists():
        return jobs
    for job_dir in settings.output_dir.iterdir():
        meta_path = job_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            jobs.append(JobMetadata.model_validate_json(meta_path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return jobs


def _cleanup_summary(deleted_job_ids: list[str] | None = None, bytes_before: int | None = None) -> OutputCleanupResponse:
    jobs = _load_job_metadata()
    total_jobs = len(jobs)
    terminal_jobs = sum(1 for job in jobs if job.status in TERMINAL_STATUSES)
    active_jobs = total_jobs - terminal_jobs
    bytes_after = _dir_size(settings.output_dir)
    deleted = deleted_job_ids or []
    return OutputCleanupResponse(
        output_dir=str(settings.output_dir),
        total_jobs=total_jobs,
        terminal_jobs=terminal_jobs,
        active_jobs=active_jobs,
        deleted_jobs=len(deleted),
        deleted_job_ids=deleted,
        retained_jobs=total_jobs,
        bytes_before=bytes_after if bytes_before is None else bytes_before,
        bytes_after=bytes_after,
    )


@app.get("/api/outputs/summary", response_model=OutputCleanupResponse)
async def output_summary() -> OutputCleanupResponse:
    return _cleanup_summary()


@app.post("/api/outputs/cleanup", response_model=OutputCleanupResponse)
async def cleanup_outputs(payload: OutputCleanupRequest) -> OutputCleanupResponse:
    bytes_before = _dir_size(settings.output_dir)
    cutoff = datetime.now(UTC) - timedelta(days=payload.older_than_days)
    deleted: list[str] = []
    for metadata in _load_job_metadata():
        if metadata.status not in TERMINAL_STATUSES:
            continue
        if metadata.status == "failed" and not payload.include_failed:
            continue
        if metadata.status == "stopped" and not payload.include_stopped:
            continue
        if not payload.delete_all_terminal and metadata.created_at > cutoff:
            continue
        control = job_controls.get(metadata.job_id)
        if control is not None and control.task is not None and not control.task.done():
            continue
        try:
            job_store.delete(metadata.job_id)
            job_controls.pop(metadata.job_id, None)
            deleted.append(metadata.job_id)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete job '{metadata.job_id}'") from exc
    return _cleanup_summary(deleted, bytes_before)


@app.get("/api/jobs")
async def list_jobs() -> list[dict]:
    """List all jobs sorted newest first."""
    jobs = []
    for job_dir in settings.output_dir.iterdir():
        meta_path = job_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = JobMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
            jobs.append({
                "job_id": meta.job_id,
                "status": meta.status,
                "label": meta.extra.get("label", ""),
                "job_title": meta.extra.get("job_title", ""),
                "created_at": meta.created_at.isoformat(),
                "cover_letter_exists": (job_dir / "cover-letter.txt").exists(),
            })
        except Exception:
            continue
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    try:
        metadata = job_store.load(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    control = job_controls.get(job_id)
    if control is not None and control.task is not None and not control.task.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is still stopping. Try again in a few seconds.",
        )
    if metadata.status not in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stop the job before deleting it.",
        )
    try:
        job_store.delete(job_id)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete job '{job_id}'") from exc
    job_controls.pop(job_id, None)


@app.post("/api/jobs/{job_id}/stop", response_model=JobMetadata)
async def stop_job(job_id: str) -> JobMetadata:
    try:
        metadata = job_store.load(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

    if metadata.status in TERMINAL_STATUSES:
        return metadata

    control = _job_control(job_id)
    control.cancel_event.set()
    updated = _stop_metadata(metadata)
    if control.task is not None and not control.task.done():
        control.task.cancel()
    return updated


@app.get("/api/jobs/by-label/{label}", response_model=JobMetadata)
async def get_job_by_label(label: str) -> JobMetadata:
    """Return the most recent completed job whose label exactly matches the search string."""
    matches: list[JobMetadata] = []
    normalized_label = _normalize_lookup(label)
    for job_dir in settings.output_dir.iterdir():
        meta_path = job_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = JobMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
            stored = _normalize_lookup(meta.extra.get("label", ""))
            if normalized_label and stored == normalized_label:
                matches.append(meta)
        except Exception:
            continue
    if not matches:
        raise HTTPException(status_code=404, detail=f"No job found with label '{label}'")
    matches.sort(key=lambda m: m.created_at)
    completed = [m for m in matches if m.status == "completed"]
    return completed[-1] if completed else matches[-1]


@app.get("/api/jobs/by-id/{short_id}", response_model=JobMetadata)
async def get_job_by_short_id(short_id: str) -> JobMetadata:
    """Find a job by exact id or a unique id prefix."""
    exact_match: JobMetadata | None = None
    prefix_matches: list[JobMetadata] = []
    for job_dir in settings.output_dir.iterdir():
        meta_path = job_dir / "metadata.json"
        if not meta_path.exists():
            continue
        meta = JobMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
        if meta.job_id == short_id:
            exact_match = meta
            break
        if meta.job_id.startswith(short_id):
            prefix_matches.append(meta)
    if exact_match is not None:
        return exact_match
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"More than one job matches id prefix '{short_id}'",
        )
    raise HTTPException(status_code=404, detail=f"No job found with id prefix '{short_id}'")


@app.get("/api/jobs/{job_id}", response_model=JobMetadata)
async def get_job(job_id: str) -> JobMetadata:
    try:
        return job_store.load(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


def _score_report_text(report: ResumeScoreReport) -> str:
    strengths = "\n".join(f"- {item}" for item in report.strengths) or "- None"
    gaps = "\n".join(f"- {item}" for item in report.gaps) or "- None"
    recommendations = "\n".join(f"- {item}" for item in report.recommendations) or "- None"
    return (
        f"Quality score: {report.quality_score}/100\n"
        f"Match score: {report.match_score}/100\n"
        f"Band: {report.score_band}\n"
        f"Decision: {report.decision}\n\n"
        f"Summary:\n{report.summary}\n\n"
        f"Strengths:\n{strengths}\n\n"
        f"Gaps:\n{gaps}\n\n"
        f"Recommendations:\n{recommendations}\n\n"
        f"Revision brief:\n{report.revision_brief}\n"
    )


async def run_generation_job(job_id: str, payload: GenerateRequest) -> None:
    control = _job_control(job_id)
    control.task = asyncio.current_task()
    candidates = await asyncio.to_thread(
        _provider_candidates, payload.provider, payload.source_job_id
    )
    quota_failures: list[str] = []
    try:
        if not candidates:
            raise RuntimeError(
                "No live provider is available. Log Codex in inside the API container "
                "or configure an Abacus API key."
            )
        for index, provider in enumerate(candidates):
            metadata = job_store.load(job_id)
            metadata = job_store.record_status(
                metadata,
                status="running",
                stage="provider_fallback" if index else "loading_profile",
                error=None,
                extra={
                    **metadata.extra,
                    "actual_provider": provider,
                    "provider_attempt": index + 1,
                    "provider_quota_failures": quota_failures,
                },
            )
            try:
                await _run_generation_job_once(
                    job_id,
                    payload,
                    _resume_generator(provider),
                    control,
                )
                return
            except ProviderQuotaError as exc:
                quota_failures.append(f"{provider}: {str(exc)[:500]}")
                if index + 1 < len(candidates):
                    continue
                job_store.record_status(
                    job_store.load(job_id),
                    status="failed",
                    stage="failed",
                    error="All selected providers are out of credits or quota.",
                    extra={
                        **job_store.load(job_id).extra,
                        "provider_quota_failures": quota_failures,
                    },
                )
                return
    except asyncio.CancelledError:
        metadata = job_store.load(job_id)
        _stop_metadata(metadata, error="Stopped by user.")
    except Exception as exc:
        metadata = job_store.load(job_id)
        job_store.record_status(
            metadata,
            status="failed",
            stage="failed",
            error=str(exc),
        )
    finally:
        control.task = None
        job_controls.pop(job_id, None)


async def _run_generation_job_once(
    job_id: str,
    payload: GenerateRequest,
    generator: ResumeGenerator,
    control: JobControl,
) -> None:
    metadata = job_store.load(job_id)
    compile_logs: list[str] = []
    last_page_count: int | None = None
    last_typst_source = ""
    last_pdf_bytes: bytes | None = None
    compile_error = False
    stop_reason = "Stopped by user."
    last_score_report: ResumeScoreReport | None = None
    compile_feedback: str | None = None
    best_candidate: dict[str, object] | None = None

    def check_cancel() -> None:
        if control.cancel_event.is_set():
            raise asyncio.CancelledError

    def remember_best_candidate(draft: ResumeDraft, report: ResumeScoreReport | None) -> None:
        nonlocal best_candidate
        if last_page_count != 1 or not last_pdf_bytes:
            return
        score_value = 0
        if report is not None:
            score_value = report.quality_score + report.match_score
            if report.decision == "approve":
                score_value += 1000
        current_score = int(best_candidate["score"]) if best_candidate else -1
        if score_value <= current_score:
            return
        best_candidate = {
            "draft": draft.model_copy(deep=True),
            "report": report.model_copy(deep=True) if report else None,
            "typst": last_typst_source,
            "pdf": last_pdf_bytes,
            "page_count": last_page_count,
            "score": score_value,
        }

    def persist_score_report(report: ResumeScoreReport | None) -> None:
        if report is None:
            return
        job_store.write_text_artifact(job_id, "score_report.json", report.model_dump_json(indent=2))
        job_store.write_text_artifact(job_id, "score_report.txt", _score_report_text(report))

    def finalize_approved_success(draft: ResumeDraft) -> None:
        nonlocal metadata
        if not last_pdf_bytes:
            raise RuntimeError("Missing compiled PDF bytes for approved draft.")
        output_base = _output_basename()
        job_store.write_text_artifact(job_id, "resume.typ", last_typst_source)
        job_store.write_binary_artifact(job_id, "resume.pdf", last_pdf_bytes)
        job_store.write_text_artifact(job_id, f"{output_base}.typ", last_typst_source)
        job_store.write_binary_artifact(job_id, f"{output_base}.pdf", last_pdf_bytes)
        job_store.write_text_artifact(job_id, "draft.json", draft.model_dump_json(indent=2))

        ts = metadata.created_at
        archive_name = "-".join([
            job_id[:8],
            ts.strftime("%H%M"),
            ts.strftime("%Y%m%d"),
            _safe_part(payload.label or "unknown"),
            _safe_part(draft.job_title or "resume"),
        ])
        job_store.write_text_artifact(job_id, f"{archive_name}.typ", last_typst_source)
        job_store.write_binary_artifact(job_id, f"{archive_name}.pdf", last_pdf_bytes)
        source_url = payload.source_url
        txt = (
            f"Source URL: {source_url}\n\n"
            if source_url else ""
        ) + f"--- JOB DESCRIPTION ---\n{payload.job_description}"
        job_store.write_text_artifact(job_id, f"{archive_name}.txt", txt)

        metadata = job_store.record_status(
            metadata,
            status="completed",
            stage="completed",
            page_count=1,
            compile_logs=compile_logs,
            pdf_url=f"/files/{job_id}/{_output_basename()}.pdf",
            typst_url=f"/files/{job_id}/{_output_basename()}.typ",
            fit_summary=draft.fit_summary,
            keywords=draft.keywords,
            extra={
                **metadata.extra,
                "job_title": draft.job_title,
                "archive_name": archive_name,
            },
        )

    def finalize_success(draft: ResumeDraft, report: ResumeScoreReport | None) -> None:
        nonlocal metadata
        if not last_pdf_bytes:
            raise RuntimeError("Missing compiled PDF bytes for final draft.")
        output_base = _output_basename()
        job_store.write_text_artifact(job_id, "resume.typ", last_typst_source)
        job_store.write_binary_artifact(job_id, "resume.pdf", last_pdf_bytes)
        job_store.write_text_artifact(job_id, f"{output_base}.typ", last_typst_source)
        job_store.write_binary_artifact(job_id, f"{output_base}.pdf", last_pdf_bytes)
        job_store.write_text_artifact(job_id, "draft.json", draft.model_dump_json(indent=2))
        persist_score_report(report)

        ts = metadata.created_at
        archive_name = "-".join([
            job_id[:8],
            ts.strftime("%H%M"),
            ts.strftime("%Y%m%d"),
            _safe_part(payload.label or "unknown"),
            _safe_part(draft.job_title or "resume"),
        ])
        job_store.write_text_artifact(job_id, f"{archive_name}.typ", last_typst_source)
        job_store.write_binary_artifact(job_id, f"{archive_name}.pdf", last_pdf_bytes)
        source_url = payload.source_url
        txt = (
            f"Source URL: {source_url}\n\n"
            if source_url else ""
        ) + f"--- JOB DESCRIPTION ---\n{payload.job_description}"
        job_store.write_text_artifact(job_id, f"{archive_name}.txt", txt)

        metadata = job_store.record_status(
            metadata,
            status="completed",
            stage="completed",
            page_count=1,
            compile_logs=compile_logs,
            pdf_url=f"/files/{job_id}/{_output_basename()}.pdf",
            typst_url=f"/files/{job_id}/{_output_basename()}.typ",
            fit_summary=draft.fit_summary,
            review_summary=report.summary if report else None,
            quality_score=report.quality_score if report else None,
            match_score=report.match_score if report else None,
            score_band=report.score_band if report else None,
            keywords=draft.keywords,
            recommendations=report.recommendations if report else [],
            extra={
                **metadata.extra,
                "job_title": draft.job_title,
                "archive_name": archive_name,
                "score_report_json_url": f"/files/{job_id}/score_report.json" if report else None,
                "score_report_txt_url": f"/files/{job_id}/score_report.txt" if report else None,
            },
        )

    async def compile_exact_draft(draft: ResumeDraft, *, stage: str) -> bool:
        nonlocal metadata, last_typst_source, last_pdf_bytes, last_page_count, compile_error, compile_logs
        check_cancel()
        last_typst_source = render_resume(profile.template, draft, profile.personalization)
        metadata = job_store.record_status(
            metadata,
            stage=stage,
            attempts=1,
            keywords=draft.keywords,
            fit_summary=draft.fit_summary,
            error=None,
        )
        compile_result = await compiler_client.compile(last_typst_source)
        check_cancel()
        compile_logs.append(
            f"Attempt 1: success={compile_result.success}, "
            f"page_count={compile_result.page_count}, "
            f"log={compile_result.compile_log.strip() or 'n/a'}"
        )
        last_page_count = compile_result.page_count
        last_pdf_bytes = (
            base64.b64decode(compile_result.pdf_base64)
            if compile_result.pdf_base64
            else None
        )
        if not compile_result.success:
            compile_error = True
            metadata = job_store.record_status(metadata, compile_logs=compile_logs)
            return False
        return bool(compile_result.page_count == 1 and last_pdf_bytes)

    # Load previous draft when editing an existing CV
    draft: ResumeDraft | None = payload.approved_draft
    if draft is None and payload.source_job_id:
        draft_path = settings.output_dir / payload.source_job_id / "draft.json"
        if draft_path.exists():
            draft = ResumeDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))

    try:
        if metadata.status == "stopped":
            return
        check_cancel()
        metadata = job_store.record_status(
            metadata,
            status="running",
            stage="loading_profile",
            attempts=0,
            error=None,
        )
        profile = await asyncio.to_thread(load_profile_data, _profile_dir())
        check_cancel()

        if payload.approved_draft is not None:
            approved_draft = payload.approved_draft
            auto_compress_attempted = False
            success = await compile_exact_draft(approved_draft, stage="compiling_approved_draft")
            if not success and not compile_error and (last_page_count or 0) > 1:
                auto_compress_attempted = True
                compile_logs.append("Approved edit overflowed one page; attempting automatic compression retry.")
                metadata = job_store.record_status(
                    metadata,
                    stage="compressing_approved_draft",
                    compile_logs=compile_logs,
                    error=None,
                )
                merged_edit_instructions = "\n\n".join(
                    part for part in [
                        (payload.edit_instructions or "").strip(),
                        APPROVED_EDIT_ONE_PAGE_INSTRUCTIONS,
                    ] if part
                )
                retry_payload = payload.model_copy(
                    update={
                        "approved_draft": None,
                        "edit_instructions": merged_edit_instructions,
                    }
                )
                compile_retry_feedback = (
                    f"success=True, page_count={last_page_count}, "
                    f"log={compile_logs[-2] if len(compile_logs) >= 2 else 'n/a'}"
                )
                approved_draft = await asyncio.to_thread(
                    generator.generate_resume,
                    profile,
                    retry_payload,
                    2,
                    payload.approved_draft,
                    None,
                    compile_retry_feedback,
                )
                success = await compile_exact_draft(
                    approved_draft,
                    stage="compiling_approved_draft_retry",
                )

            if success and last_pdf_bytes:
                finalize_approved_success(approved_draft)
                return

            if last_typst_source:
                job_store.write_text_artifact(job_id, "resume.typ", last_typst_source)
            if last_pdf_bytes:
                job_store.write_binary_artifact(job_id, "resume.pdf", last_pdf_bytes)
            error_msg = (
                "Approved edit preview failed Typst compilation."
                if compile_error
                else (
                    "Approved edit preview still did not fit on one page after an automatic compression retry. "
                    "Edit it again and re-approve, or use Shrink to one page."
                    if auto_compress_attempted
                    else "Approved edit preview did not fit on one page. Edit it again and re-approve."
                )
            )
            job_store.record_status(
                metadata,
                status="failed",
                stage="failed",
                page_count=last_page_count,
                compile_logs=compile_logs,
                pdf_url=f"/files/{job_id}/resume.pdf" if last_pdf_bytes else None,
                typst_url=f"/files/{job_id}/resume.typ" if last_typst_source else None,
                error=error_msg,
                fit_summary=approved_draft.fit_summary,
                keywords=approved_draft.keywords,
            )
            return

        max_generator_attempts = max(1, settings.max_generator_retries)
        max_scorer_attempts = max(1, settings.max_scorer_retries)
        scorer_feedback: ResumeScoreReport | None = None

        for attempt in range(1, max_generator_attempts + 1):
            check_cancel()
            metadata = job_store.record_status(
                metadata,
                stage=f"gen_attempt_{attempt}",
                attempts=attempt,
                compile_logs=compile_logs,
                review_summary=scorer_feedback.summary if scorer_feedback else None,
                quality_score=scorer_feedback.quality_score if scorer_feedback else None,
                match_score=scorer_feedback.match_score if scorer_feedback else None,
                score_band=scorer_feedback.score_band if scorer_feedback else None,
                recommendations=scorer_feedback.recommendations if scorer_feedback else [],
            )
            draft = await asyncio.to_thread(
                generator.generate_resume,
                profile,
                payload,
                attempt,
                draft,
                scorer_feedback,
                compile_feedback,
            )
            check_cancel()
            last_typst_source = render_resume(profile.template, draft, profile.personalization)
            metadata = job_store.record_status(
                metadata,
                stage=f"compile_attempt_{attempt}",
                keywords=draft.keywords,
                fit_summary=draft.fit_summary,
            )

            compile_result = await compiler_client.compile(last_typst_source)
            check_cancel()
            compile_feedback = (
                f"success={compile_result.success}, "
                f"page_count={compile_result.page_count}, "
                f"log={compile_result.compile_log.strip() or 'n/a'}"
            )
            compile_logs.append(f"gen {attempt}: {compile_feedback}")
            last_page_count = compile_result.page_count
            last_pdf_bytes = (
                base64.b64decode(compile_result.pdf_base64)
                if compile_result.pdf_base64
                else None
            )

            if not compile_result.success:
                compile_error = True
                metadata = job_store.record_status(
                    metadata,
                    compile_logs=compile_logs,
                )
                break  # syntax/compile error — compression retries won't help

            if attempt <= max_scorer_attempts:
                metadata = job_store.record_status(
                    metadata,
                    stage=f"scr_attempt_{attempt}",
                    page_count=last_page_count,
                )
                scorer_feedback = await asyncio.to_thread(
                    generator.score_resume,
                    profile,
                    payload,
                    draft,
                    attempt,
                    attempt,
                    compile_feedback,
                )
                last_score_report = scorer_feedback
                persist_score_report(scorer_feedback)
                compile_logs.append(
                    f"scr {attempt}: quality={scorer_feedback.quality_score}, "
                    f"match={scorer_feedback.match_score}, decision={scorer_feedback.decision}"
                )
                metadata = job_store.record_status(
                    metadata,
                    review_summary=scorer_feedback.summary,
                    quality_score=scorer_feedback.quality_score,
                    match_score=scorer_feedback.match_score,
                    score_band=scorer_feedback.score_band,
                    recommendations=scorer_feedback.recommendations,
                    compile_logs=compile_logs,
                )

            if compile_result.page_count == 1 and last_pdf_bytes:
                remember_best_candidate(draft, scorer_feedback)
                if scorer_feedback is None or scorer_feedback.decision == "approve":
                    finalize_success(draft, scorer_feedback)
                    return

        if best_candidate is not None:
            final_draft = best_candidate["draft"]
            final_report = best_candidate["report"]
            final_typst = best_candidate["typst"]
            final_pdf = best_candidate["pdf"]
            final_page_count = best_candidate["page_count"]
            if isinstance(final_draft, ResumeDraft) and isinstance(final_typst, str) and isinstance(final_pdf, bytes):
                last_typst_source = final_typst
                last_pdf_bytes = final_pdf
                last_page_count = final_page_count if isinstance(final_page_count, int) else 1
                finalize_success(final_draft, final_report if isinstance(final_report, ResumeScoreReport) else None)
                return

        if last_typst_source:
            job_store.write_text_artifact(job_id, "resume.typ", last_typst_source)
        if last_pdf_bytes:
            job_store.write_binary_artifact(job_id, "resume.pdf", last_pdf_bytes)
        persist_score_report(last_score_report)

        error_msg = (
            "Typst compilation failed — check the compile log for syntax errors."
            if compile_error
            else "Could not produce a strong one-page resume within the gen/scr retry limit."
        )
        job_store.record_status(
            metadata,
            status="failed",
            stage="failed",
            page_count=last_page_count,
            compile_logs=compile_logs,
            pdf_url=f"/files/{job_id}/resume.pdf" if last_pdf_bytes else None,
            typst_url=f"/files/{job_id}/resume.typ" if last_typst_source else None,
            error=error_msg,
            fit_summary=draft.fit_summary if draft else None,
            review_summary=last_score_report.summary if last_score_report else None,
            quality_score=last_score_report.quality_score if last_score_report else None,
            match_score=last_score_report.match_score if last_score_report else None,
            score_band=last_score_report.score_band if last_score_report else None,
            keywords=draft.keywords if draft else [],
            recommendations=last_score_report.recommendations if last_score_report else [],
        )
    except ProviderQuotaError:
        raise
    except asyncio.CancelledError:
        if last_typst_source:
            job_store.write_text_artifact(job_id, "resume.typ", last_typst_source)
        if last_pdf_bytes:
            job_store.write_binary_artifact(job_id, "resume.pdf", last_pdf_bytes)
        persist_score_report(last_score_report)
        _stop_metadata(
            metadata,
            error=stop_reason,
            page_count=last_page_count,
            compile_logs=compile_logs,
            pdf_url=f"/files/{job_id}/resume.pdf" if last_pdf_bytes else metadata.pdf_url,
            typst_url=f"/files/{job_id}/resume.typ" if last_typst_source else metadata.typst_url,
        )
        return
    except Exception as exc:  # pragma: no cover - defensive path
        if last_typst_source:
            job_store.write_text_artifact(job_id, "resume.typ", last_typst_source)
        if last_pdf_bytes:
            job_store.write_binary_artifact(job_id, "resume.pdf", last_pdf_bytes)
        persist_score_report(last_score_report)
        job_store.record_status(
            metadata,
            status="failed",
            stage="failed",
            page_count=last_page_count,
            compile_logs=compile_logs,
            error=str(exc),
            review_summary=last_score_report.summary if last_score_report else None,
            quality_score=last_score_report.quality_score if last_score_report else None,
            match_score=last_score_report.match_score if last_score_report else None,
            score_band=last_score_report.score_band if last_score_report else None,
            recommendations=last_score_report.recommendations if last_score_report else [],
        )
