from __future__ import annotations

import json
import re
from collections import Counter

import httpx
from openai import OpenAI

from app.data_loader import (
    ProfileData,
    extract_contact_fields,
    extract_education_fields,
)
from app.models import GenerateRequest
from app.prompting import (
    build_generator_system_prompt,
    build_generator_user_prompt,
    build_scorer_system_prompt,
    build_scorer_user_prompt,
)
from app.review_schema import SCORE_REPORT_SCHEMA, ResumeScoreReport
from app.resume_schema import RESUME_SCHEMA, ProjectEntry, ResumeDraft, SkillBucket


STOPWORDS = {
    "about",
    "across",
    "after",
    "also",
    "and",
    "build",
    "building",
    "for",
    "from",
    "have",
    "into",
    "looking",
    "must",
    "role",
    "that",
    "their",
    "this",
    "with",
    "your",
}



class ResumeGenerator:
    def __init__(
        self,
        provider: str,
        api_key: str | None,
        abacus_api_key: str | None,
        abacus_base_url: str,
        model_name: str,
        reasoning_effort: str,
        enable_demo_mode: bool,
    ) -> None:
        self.provider = provider
        self.client = OpenAI(api_key=api_key) if provider == "openai" and api_key else None
        self.abacus_api_key = abacus_api_key
        self.abacus_base_url = abacus_base_url.rstrip("/")
        self.model_name = model_name
        self.reasoning_effort = reasoning_effort
        self.enable_demo_mode = enable_demo_mode

    def has_live_model(self) -> bool:
        if self.provider == "abacus":
            return self.abacus_api_key is not None
        return self.client is not None

    def generate_resume(
        self,
        profile: ProfileData,
        request: GenerateRequest,
        attempt: int,
        previous_draft: ResumeDraft | None,
        scorer_feedback: ResumeScoreReport | None = None,
        compile_feedback: str | None = None,
    ) -> ResumeDraft:
        if previous_draft is not None:
            previous_draft = enforce_active_profile_scope(profile, previous_draft)
        if self.provider == "abacus" and self.abacus_api_key is not None:
            draft = self._generate_with_abacus(
                profile,
                request,
                attempt,
                previous_draft,
                scorer_feedback,
                compile_feedback,
            )
        elif self.client is not None:
            draft = self._generate_with_openai(
                profile,
                request,
                attempt,
                previous_draft,
                scorer_feedback,
                compile_feedback,
            )
        elif self.enable_demo_mode:
            draft = self._generate_demo_resume(profile, request)
        else:
            raise RuntimeError(
                "No live LLM credentials are configured and ENABLE_DEMO_MODE is false."
            )
        return enforce_active_profile_scope(profile, draft)

    def score_resume(
        self,
        profile: ProfileData,
        request: GenerateRequest,
        draft: ResumeDraft,
        generator_attempt: int,
        scorer_attempt: int,
        compile_feedback: str | None = None,
    ) -> ResumeScoreReport:
        if self.provider == "abacus" and self.abacus_api_key is not None:
            report = self._score_with_abacus(
                profile,
                request,
                draft,
                generator_attempt,
                scorer_attempt,
                compile_feedback,
            )
        elif self.client is not None:
            report = self._score_with_openai(
                profile,
                request,
                draft,
                generator_attempt,
                scorer_attempt,
                compile_feedback,
            )
        elif self.enable_demo_mode:
            report = self._score_demo_resume(request, draft, compile_feedback)
        else:
            raise RuntimeError(
                "No live LLM credentials are configured and ENABLE_DEMO_MODE is false."
            )
        report = apply_semester_average_score(report, draft, profile)
        return apply_profile_requirement_score(report, draft, profile)

    def _generate_with_openai(
        self,
        profile: ProfileData,
        request: GenerateRequest,
        attempt: int,
        previous_draft: ResumeDraft | None,
        scorer_feedback: ResumeScoreReport | None,
        compile_feedback: str | None,
    ) -> ResumeDraft:
        response = self.client.responses.create(
            model=self.model_name,
            reasoning={"effort": self.reasoning_effort},
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_generator_system_prompt(profile),
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_generator_user_prompt(
                                profile,
                                request,
                                attempt,
                                previous_draft,
                                scorer_feedback,
                                compile_feedback,
                            ),
                        }
                    ],
                },
            ],
            text={"format": {"type": "json_schema", **RESUME_SCHEMA}},
        )
        payload = response.output_text
        if not payload:
            raise RuntimeError("OpenAI returned an empty response.")
        return ResumeDraft.model_validate(json.loads(payload))

    def _generate_with_abacus(
        self,
        profile: ProfileData,
        request: GenerateRequest,
        attempt: int,
        previous_draft: ResumeDraft | None,
        scorer_feedback: ResumeScoreReport | None,
        compile_feedback: str | None,
    ) -> ResumeDraft:
        prompt = build_generator_user_prompt(
            profile,
            request,
            attempt,
            previous_draft,
            scorer_feedback,
            compile_feedback,
        )
        schema_json = json.dumps(RESUME_SCHEMA["schema"], ensure_ascii=False)
        response = httpx.post(
            f"{self.abacus_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.abacus_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name,
                "stream": False,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"{build_generator_system_prompt(profile)} "
                            "You must return one valid JSON object only. "
                            f"It must satisfy this JSON Schema exactly: {schema_json}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            },
            timeout=120.0,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Abacus response payload: {payload}") from exc
        if not content:
            raise RuntimeError("Abacus returned an empty response.")
        return ResumeDraft.model_validate(json.loads(content))

    def _score_with_openai(
        self,
        profile: ProfileData,
        request: GenerateRequest,
        draft: ResumeDraft,
        generator_attempt: int,
        scorer_attempt: int,
        compile_feedback: str | None,
    ) -> ResumeScoreReport:
        response = self.client.responses.create(
            model=self.model_name,
            reasoning={"effort": self.reasoning_effort},
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_scorer_system_prompt(profile),
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_scorer_user_prompt(
                                profile,
                                request,
                                draft,
                                generator_attempt,
                                scorer_attempt,
                                compile_feedback,
                            ),
                        }
                    ],
                },
            ],
            text={"format": {"type": "json_schema", **SCORE_REPORT_SCHEMA}},
        )
        payload = response.output_text
        if not payload:
            raise RuntimeError("OpenAI returned an empty scorer response.")
        return ResumeScoreReport.model_validate(json.loads(payload))

    def _score_with_abacus(
        self,
        profile: ProfileData,
        request: GenerateRequest,
        draft: ResumeDraft,
        generator_attempt: int,
        scorer_attempt: int,
        compile_feedback: str | None,
    ) -> ResumeScoreReport:
        prompt = build_scorer_user_prompt(
            profile,
            request,
            draft,
            generator_attempt,
            scorer_attempt,
            compile_feedback,
        )
        schema_json = json.dumps(SCORE_REPORT_SCHEMA["schema"], ensure_ascii=False)
        response = httpx.post(
            f"{self.abacus_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.abacus_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name,
                "stream": False,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"{build_scorer_system_prompt(profile)} "
                            "You must return one valid JSON object only. "
                            f"It must satisfy this JSON Schema exactly: {schema_json}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            },
            timeout=120.0,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Abacus response payload: {payload}") from exc
        if not content:
            raise RuntimeError("Abacus returned an empty scorer response.")
        return ResumeScoreReport.model_validate(json.loads(content))

    def _generate_demo_resume(
        self,
        profile: ProfileData,
        request: GenerateRequest,
    ) -> ResumeDraft:
        keywords = extract_keywords(request.job_description)
        contact = extract_contact_fields(profile.master_profile)
        education = extract_education_fields(profile.master_profile)
        scored_projects = sorted(
            profile.projects,
            key=lambda item: project_score(item, request.role_focus, keywords),
            reverse=True,
        )
        selected_projects = scored_projects[:3]

        project_entries = []
        for item in selected_projects:
            bullets = item.get("facts", [])[:3]
            project_entries.append(
                {
                    "title": item["title"],
                    "stack": item.get("stack", [])[:2],
                    "dates": item.get("dates", ""),
                    "bullets": bullets,
                }
            )

        skill_buckets = []
        for category, items in profile.skills.items():
            filtered = [
                item for item in items
                if keep_skill(item, category, keywords, request.role_focus)
            ]
            if filtered:
                skill_buckets.append(SkillBucket(category=category.title(), items=filtered[:5]))
        if not skill_buckets:
            skill_buckets = [
                SkillBucket(category=category.title(), items=items[:5])
                for category, items in profile.skills.items()
            ]

        summary_line = profile.master_profile.split("## Summary Facts", maxsplit=1)[-1]
        summary_bits = [
            line.removeprefix("* ").strip()
            for line in summary_line.splitlines()
            if line.strip().startswith("* ")
        ][:2]
        profile_text = " ".join(summary_bits) or (
            "Engineering student with strong systems and software interests."
        )
        if profile.personalization.required_profile_phrase and not profile_has_deans_list(profile_text, profile):
            profile_text = f"{profile.personalization.required_profile_phrase} {profile_text}".strip()

        return ResumeDraft.model_validate(
            {
                "contact": {
                    "name": contact.get("name", "Your Name"),
                    "email": contact.get("email", ""),
                    "location": contact.get("location", ""),
                    "linkedin": contact.get("linkedin", ""),
                    "github": contact.get("github", ""),
                },
                "headline": "",
                "profile": profile_text,
                "education": education,
                "projects": project_entries,
                "skills": [bucket.model_dump() for bucket in skill_buckets[:4]],
                "fit_summary": (
                    "Draft generated in demo mode from local profile data. "
                    "Add OpenAI or Abacus credentials for live tailoring."
                ),
                "keywords": keywords[:10],
                "job_title": request.role_focus.title() if request.role_focus else "",
            }
        )

    def _score_demo_resume(
        self,
        request: GenerateRequest,
        draft: ResumeDraft,
        compile_feedback: str | None,
    ) -> ResumeScoreReport:
        keywords = extract_keywords(request.job_description)
        draft_text = json.dumps(draft.model_dump(), ensure_ascii=False).lower()
        keyword_hits = sum(1 for keyword in keywords[:8] if keyword in draft_text)
        project_count = len(draft.projects)
        compile_issue = bool(compile_feedback and "page_count=1" not in compile_feedback.lower())
        quality_score = 72 + min(keyword_hits * 3, 12)
        match_score = 68 + min(keyword_hits * 4, 20)
        gaps: list[str] = []
        recommendations: list[str] = []

        if compile_issue:
            quality_score -= 8
            gaps.append("The rendered resume still needs stronger one-page compression.")
            recommendations.append("Shorten the profile and trim weaker bullets before shipping.")
        if project_count > 4:
            quality_score -= 5
            gaps.append("Too many projects weaken scan speed for a one-page student CV.")
            recommendations.append("Keep only the strongest role-relevant projects.")
        if keyword_hits < 3:
            match_score -= 10
            gaps.append("The draft does not surface enough role-relevant keywords near the top.")
            recommendations.append("Mirror exact supported job-title and tool language earlier in the resume.")
        if not recommendations:
            recommendations.append("Keep bullets specific about tools, algorithms, constraints, or outcomes.")
        if not gaps:
            gaps.append("A few bullets could still show sharper scope or results.")

        quality_score = max(0, min(100, quality_score))
        match_score = max(0, min(100, match_score))
        average_score = (quality_score + match_score) / 2
        if average_score >= 90:
            score_band = "Excellent"
        elif average_score >= 80:
            score_band = "Strong"
        elif average_score >= 70:
            score_band = "Good"
        elif average_score >= 60:
            score_band = "Moderate"
        else:
            score_band = "Weak"
        decision = "approve" if quality_score >= 80 and match_score >= 78 and not compile_issue else "revise"
        return ResumeScoreReport(
            quality_score=quality_score,
            match_score=match_score,
            score_band=score_band,
            decision=decision,
            summary=(
                "Demo-mode score based on keyword overlap, one-page discipline, and overall draft focus. "
                "Use a live model for stronger scoring and revision feedback."
            ),
            strengths=[
                "Draft uses grounded profile data and ATS-safe structured sections.",
                "Keyword extraction is tied to the pasted job description.",
            ],
            gaps=gaps[:4],
            recommendations=recommendations[:4],
            revision_brief="Tighten role keywords, reduce weaker bullets, and sharpen action-method-result wording without inventing facts.",
        )


def extract_keywords(job_description: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+.-]{2,}", job_description.lower())
    counts = Counter(token for token in tokens if token not in STOPWORDS)
    return [token for token, _ in counts.most_common(10)]


def contains_forbidden_draft_term(value: str, profile: ProfileData) -> bool:
    pattern = profile.personalization.forbidden_content_regex.strip()
    return bool(pattern and re.search(pattern, value, flags=re.IGNORECASE))


def filter_forbidden_sentences(value: str, profile: ProfileData) -> str:
    pieces = re.split(r"(?<=[.!?])\s+", value.strip())
    kept = [piece for piece in pieces if piece and not contains_forbidden_draft_term(piece, profile)]
    return " ".join(kept).strip()


def filter_forbidden_items(items: list[str], profile: ProfileData) -> list[str]:
    return [item for item in items if not contains_forbidden_draft_term(item, profile)]


def contains_semester_average_text(value: str, profile: ProfileData) -> bool:
    return contains_forbidden_draft_term(value, profile)


def strip_semester_average_text(value: str, profile: ProfileData) -> str:
    pattern = profile.personalization.forbidden_content_regex.strip()
    if not pattern:
        return value.strip()
    stripped = re.sub(pattern, "", value, flags=re.IGNORECASE)
    stripped = re.sub(r"\s+([,.;:])", r"\1", stripped)
    stripped = re.sub(r"^\s*[;|,.-]+\s*", "", stripped)
    stripped = re.sub(r"\s*[;|,.-]+\s*$", "", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped)
    return stripped.strip()


def strip_semester_average_items(items: list[str], profile: ProfileData) -> list[str]:
    cleaned = [strip_semester_average_text(item, profile) for item in items]
    return [item for item in cleaned if item]


def project_from_profile_item(project: dict) -> ProjectEntry:
    return ProjectEntry(
        title=str(project.get("title", "")),
        stack=[str(item) for item in project.get("stack", [])[:2]],
        dates=str(project.get("dates", "")),
        bullets=[str(item) for item in project.get("facts", [])[:3] if str(item).strip()],
    )


def enforce_active_profile_scope(profile: ProfileData, draft: ResumeDraft) -> ResumeDraft:
    active_projects = {
        str(project.get("title", "")): project
        for project in profile.projects
        if str(project.get("title", "")).strip()
    }
    allowed_titles = set(active_projects)
    projects = [
        project
        for project in draft.projects
        if project.title in allowed_titles and not contains_forbidden_draft_term(project.title, profile)
    ]

    if len(projects) < 2:
        used_titles = {project.title for project in projects}
        for title, project in active_projects.items():
            if title in used_titles:
                continue
            fallback = project_from_profile_item(project)
            if fallback.bullets:
                projects.append(fallback)
                used_titles.add(title)
            if len(projects) >= 2:
                break

    skills = []
    for bucket in draft.skills:
        if contains_forbidden_draft_term(bucket.category, profile):
            continue
        category = strip_semester_average_text(bucket.category, profile)
        if not category:
            continue
        items = strip_semester_average_items(filter_forbidden_items(bucket.items, profile), profile)
        if items:
            skills.append(bucket.model_copy(update={"category": category, "items": items}))

    profile_text = strip_semester_average_text(filter_forbidden_sentences(draft.profile, profile), profile)
    if not profile_text:
        profile_text = strip_semester_average_text(draft.profile, profile)
    if contains_forbidden_draft_term(profile_text, profile) or not profile_has_deans_list(profile_text, profile):
        profile_text = (profile.personalization.default_profile_text or "Engineering candidate with verified project experience across software, systems, and applied technical work.")

    education = draft.education.model_copy(
        update={"bullets": strip_semester_average_items(draft.education.bullets, profile)}
    )

    cleaned_projects = []
    for project in projects:
        bullets = strip_semester_average_items(project.bullets, profile)
        if bullets:
            cleaned_projects.append(project.model_copy(update={"bullets": bullets}))

    fit_summary = strip_semester_average_text(filter_forbidden_sentences(draft.fit_summary, profile), profile)
    if len(fit_summary.split()) < 4:
        fit_summary = "This draft uses only active verified project evidence from the current profile scope."

    return draft.model_copy(
        update={
            "profile": profile_text,
            "education": education,
            "projects": cleaned_projects,
            "skills": skills,
            "fit_summary": fit_summary,
            "keywords": strip_semester_average_items(filter_forbidden_items(draft.keywords, profile), profile),
        }
    )


def profile_has_deans_list(profile_text: str, profile: ProfileData) -> bool:
    phrase = profile.personalization.required_profile_phrase.strip()
    if not phrase:
        return True
    plain_phrase = re.sub(r"#(?:strong|emph)\[(.*?)\]", r"\1", phrase).strip()
    return bool(plain_phrase and re.search(re.escape(plain_phrase), profile_text, flags=re.IGNORECASE))


def score_band(quality_score: int, match_score: int) -> str:
    average_score = (quality_score + match_score) / 2
    if average_score >= 90:
        return "Excellent"
    if average_score >= 80:
        return "Strong"
    if average_score >= 70:
        return "Good"
    if average_score >= 60:
        return "Moderate"
    return "Weak"


def append_unique(items: list[str], value: str) -> list[str]:
    if any(item.strip().lower() == value.lower() for item in items):
        return items
    return [*items, value]


def apply_profile_requirement_score(
    report: ResumeScoreReport,
    draft: ResumeDraft,
    profile: ProfileData,
) -> ResumeScoreReport:
    settings = profile.personalization
    if not settings.required_profile_phrase or profile_has_deans_list(draft.profile, profile):
        return report

    plain_phrase = re.sub(r"#(?:strong|emph)\[(.*?)\]", r"\1", settings.required_profile_phrase).strip()
    gap = f"The profile does not naturally include {plain_phrase}." if plain_phrase else ""
    recommendation = settings.required_profile_recommendation
    quality_score = min(report.quality_score, 74)
    revision_brief = report.revision_brief
    if recommendation and recommendation.lower() not in revision_brief.lower():
        revision_brief = f"{recommendation} {revision_brief}".strip()
    return report.model_copy(
        update={
            "quality_score": quality_score,
            "score_band": score_band(quality_score, report.match_score),
            "decision": "revise",
            "gaps": append_unique(report.gaps, gap) if gap else report.gaps,
            "recommendations": append_unique(
                report.recommendations,
                recommendation,
            ) if recommendation else report.recommendations,
            "revision_brief": revision_brief,
        }
    )


def clean_report_items(items: list[str], profile: ProfileData) -> list[str]:
    cleaned = [strip_semester_average_text(item, profile) for item in items]
    return [
        item
        for item in cleaned
        if item and not contains_semester_average_text(item, profile)
    ]


def apply_semester_average_score(
    report: ResumeScoreReport,
    draft: ResumeDraft,
    profile: ProfileData,
) -> ResumeScoreReport:
    draft_payload = json.dumps(draft.model_dump(), ensure_ascii=False)
    settings = profile.personalization
    has_duplicate = contains_semester_average_text(draft_payload, profile)

    summary = strip_semester_average_text(report.summary, profile)
    revision_brief = strip_semester_average_text(report.revision_brief, profile)
    gaps = clean_report_items(report.gaps, profile)
    recommendations = clean_report_items(report.recommendations, profile)
    strengths = clean_report_items(report.strengths, profile)

    quality_score = report.quality_score
    decision = report.decision
    if has_duplicate:
        quality_score = min(quality_score, 78)
        decision = "revise"
        if settings.forbidden_content_gap:
            gaps = append_unique(gaps, settings.forbidden_content_gap)
        if settings.forbidden_content_recommendation:
            recommendations = append_unique(recommendations, settings.forbidden_content_recommendation)
            if settings.forbidden_content_recommendation.lower() not in revision_brief.lower():
                revision_brief = f"{settings.forbidden_content_recommendation} {revision_brief}".strip()

    return report.model_copy(
        update={
            "quality_score": quality_score,
            "score_band": score_band(quality_score, report.match_score),
            "decision": decision,
            "summary": summary or report.summary,
            "strengths": strengths,
            "gaps": gaps,
            "recommendations": recommendations,
            "revision_brief": revision_brief or settings.forbidden_content_recommendation or report.revision_brief,
        }
    )


def project_score(project: dict, role_focus: str | None, keywords: list[str]) -> int:
    haystack = " ".join(
        [
            project.get("title", ""),
            project.get("summary", ""),
            " ".join(project.get("facts", [])),
            " ".join(project.get("tags", [])),
            " ".join(project.get("stack", [])),
        ]
    ).lower()
    score = sum(5 for keyword in keywords if keyword in haystack)
    if role_focus and role_focus.lower() in haystack:
        score += 10
    score -= int(project.get("selection_penalty", 0) or 0)
    return score


def keep_skill(
    item: str,
    category: str,
    keywords: list[str],
    role_focus: str | None,
) -> bool:
    text = f"{category} {item}".lower()
    if role_focus and role_focus.lower() in text:
        return True
    return any(keyword in text for keyword in keywords[:6])
