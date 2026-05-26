from __future__ import annotations

import json

from app.data_loader import ProfileData
from app.models import GenerateRequest
from app.review_schema import ResumeScoreReport
from app.resume_schema import ResumeDraft


def _private_resume_constraints(profile: ProfileData) -> str:
    settings = profile.personalization
    lines: list[str] = []
    if settings.required_profile_phrase:
        lines.append(
            f"- The Profile section must naturally include this personalized phrase when supported by the profile: {settings.required_profile_phrase}."
        )
    if settings.forbidden_content_gap or settings.forbidden_content_recommendation:
        detail = settings.forbidden_content_recommendation or settings.forbidden_content_gap
        lines.append(f"- Personalized cleanup rule: {detail}")
    if settings.fixed_education_typst:
        lines.append(
            "- The template may render fixed education or honors lines from personalization settings; do not duplicate those same details in generated JSON bullets."
        )
    if settings.extra_prompt_notes:
        lines.append("- Extra personalized notes: " + settings.extra_prompt_notes)
    return "\n".join(lines) or "- No extra personalized resume constraints are configured."


def _private_scorer_constraints(profile: ProfileData) -> str:
    settings = profile.personalization
    lines: list[str] = []
    if settings.required_profile_phrase:
        lines.append(
            f"- Penalize drafts whose profile does not naturally include the personalized required phrase: {settings.required_profile_phrase}."
        )
        if settings.required_profile_recommendation:
            lines.append(f"- If missing, recommend: {settings.required_profile_recommendation}")
    if settings.forbidden_content_gap or settings.forbidden_content_recommendation:
        lines.append(
            f"- Penalize personalized duplicate/forbidden content: {settings.forbidden_content_gap or settings.forbidden_content_recommendation}"
        )
        if settings.forbidden_content_recommendation:
            lines.append(f"- If present, recommend: {settings.forbidden_content_recommendation}")
    if settings.extra_prompt_notes:
        lines.append("- Extra personalized scoring notes: " + settings.extra_prompt_notes)
    return "\n".join(lines) or "- No extra personalized scoring constraints are configured."

def build_generator_system_prompt(profile: ProfileData) -> str:
    return f"""
# Identity
You are `gen`, a senior resume-generation agent.

# Mission
Write a tailored, ATS-safe, one-page resume from verified source material only.
Your output must be recruiter-readable in a fast scan, aligned to the target role, and interview-defensible.

# Hard Constraints
- Use only facts explicitly supported by the provided profile files.
- Never invent employers, internships, production scope, business impact, leadership scope, technologies, dates, or metrics.
- If a metric is not verified, strengthen the wording with scope, constraints, algorithms, tools, or interfaces instead of fabricating numbers.
- Treat project work as academic, coursework, personal, or independent unless the source explicitly says it was employment.
- Preserve unchanged content when a previous draft is provided and only revise what the instructions require.
- Return content that matches the provided JSON schema exactly.
- Inline formatting is allowed only inside string values using #strong[text] and #emph[text].
Personalized profile constraints:
{_private_resume_constraints(profile)}

# Writing Priorities
- Mirror the role's exact job title and the most relevant hard-skill keywords naturally when supported by the source material.
- Keep the top third sharp: clear role target, concise profile, and strong first bullets.
- Prefer bullets written as action + method/context + result.
- Use strong action verbs and concrete engineering language.
- Keep wording compact enough to fit a one-page PDF resume.
- Prefer stronger, differentiated projects first.
- Avoid filler, cliches, keyword stuffing, vague self-praise, and unsupported claims.

# Shared Guidance
{profile.cv_guidance}
""".strip()


def build_generator_user_prompt(
    profile: ProfileData,
    request: GenerateRequest,
    attempt: int,
    previous_draft: ResumeDraft | None,
    scorer_feedback: ResumeScoreReport | None,
    compile_feedback: str | None = None,
) -> str:
    retry_rules = {
        1: "Produce the strongest first-pass one-page candidate draft with the highest role match.",
        2: "Revise the previous draft using scorer and compile feedback. Tighten wording, improve ATS relevance, and compress only where needed.",
    }
    examples = json.dumps(profile.examples, indent=2, ensure_ascii=False)
    style_references = "\n\n".join(
        [
            "\n".join(
                [
                    f"Reference CV: {ref.name}",
                    "Profile style:",
                    ref.profile or "(missing)",
                    "",
                    "Project description style:",
                    ref.project_style or "(missing)",
                ]
            )
            for ref in profile.style_references
        ]
    ) or "None"
    previous = (
        json.dumps(previous_draft.model_dump(), indent=2, ensure_ascii=False)
        if previous_draft
        else "None"
    )
    review_feedback = (
        json.dumps(scorer_feedback.model_dump(), indent=2, ensure_ascii=False)
        if scorer_feedback
        else "None"
    )
    compile_notes = compile_feedback or "None"
    edit_block = (
        f"\nEdit instructions (apply these specific changes to the previous draft — do NOT regenerate from scratch):\n"
        f"{request.edit_instructions}\n"
        f"Editing rules:\n"
        f"- Start from the previous draft and change only what was requested.\n"
        f"- Keep the same job target and same verified facts unless the edit explicitly asks otherwise.\n"
        f"- For inline formatting, use raw Typst syntax directly inside the JSON string value: #strong[text] for bold, #emph[text] for italic.\n"
        f"- Do not wrap Typst syntax in backticks.\n"
        f"- Do not output Markdown formatting such as **text** or _text_.\n"
        if request.edit_instructions else ""
    )

    return f"""
Create a tailored resume draft for this job.

Attempt number: {attempt}
Attempt policy: {retry_rules.get(attempt, retry_rules[2])}
Role focus: {request.role_focus or "not specified"}
Template: {request.template}{edit_block}

Job description:
{request.job_description}

Canonical profile:
{profile.master_profile}

Project bank:
{json.dumps(profile.projects, indent=2, ensure_ascii=False)}

Skills bank:
{json.dumps(profile.skills, indent=2, ensure_ascii=False)}

Writing rules:
{profile.rules}

Preferred writing style references for profile and project wording:
Use these as the primary tone/structure reference for the profile section and project descriptions.
These references are for style only, not new facts. Do not copy unsupported claims.
{style_references}

Examples:
{examples}

Previous draft to compress or improve:
{previous}

Latest scorer feedback from `scr`:
{review_feedback}

Latest compile feedback:
{compile_notes}

Output requirements:
- Use only information supported by the source material.
- Keep the profile to at most 2 sentences.
- Set `headline` to an empty string unless a human explicitly asks for a separate profile headline.
- Do not mention GPA unless a human explicitly asks for it or the job posting clearly requires GPA disclosure.
- Do not mention the recent specialization-focused semester averages anywhere; the template hardcodes that line. This also means do not put those averages in `profile`, `education.bullets`, `fit_summary`, `keywords`, project bullets, or skills.
- Default to 4 projects on the first attempt; reduce to 3 on the second attempt if it materially improves focus or page fit; never fewer than 2.
- Project title must be the EXACT name from the project bank (do not paraphrase or abbreviate it).
- If a project includes metadata such as `selection_priority: "support_only"` or a `selection_penalty`, treat it as last-rank supporting evidence and include it only when the target role clearly benefits from it after stronger core projects.
- Project stack must contain at most 2 items — the two most relevant technologies for this role (they will be rendered as "Tech1 / Tech2").
- education.bullets should contain compact role-relevant coursework or honors details that are supported by the profile. Do not duplicate details that the configured template already renders.
- Keep project bullets crisp and interview-defensible.
- For the profile section, lean toward the tone and structure of the provided CV style references: concise, technical, specific, and grounded in systems/ML/accelerated-computing language when supported by the source material.
- In the profile section, apply any configured personalized required phrase naturally inside a sentence; do not append it as a detached suffix.
- For project descriptions, lean toward the provided CV style references: strong action verbs, explicit technical scope, and concrete engineering framing rather than generic task descriptions.
- Keep keywords relevant to ATS language from the job description.
- Preserve contact information from the profile.
- If edit instructions request bold or italic text, use only inline Typst #strong[...] or #emph[...] in the affected string fields.
- job_title: the exact job title as stated in the job posting (e.g. "Senior ML Engineer", "Backend Developer").
- fit_summary: 1-2 sentences explaining why this draft fits the role using only supported evidence.
- keywords: include the most important ATS-relevant role keywords that are genuinely supported by the draft.
""".strip()


def build_scorer_system_prompt(profile: ProfileData) -> str:
    return f"""
# Identity
You are `scr`, a strict CV scorer and resume strategist.

# Mission
Score the current draft against the target role, ATS readability, recruiter scan clarity, impact, and credibility.
Recommend only improvements that can be made from the verified source material.

# Hard Constraints
- Never ask for fabricated numbers or unsupported claims.
- Penalize vague bullets, weak targeting, keyword mismatch, bloated phrasing, and poor page discipline.
Personalized profile scoring constraints:
{_private_scorer_constraints(profile)}
- Reward clear role targeting, exact relevant keywords, strong action verbs, concrete tools/methods, differentiated projects, and measurable outcomes when verified.
- If numbers are unavailable, recommend sharper scope/context wording rather than invented metrics.
- Approve only when the draft is strong enough to ship as-is.
- Keep recommendations practical and prioritized.

# Shared Guidance
{profile.cv_guidance}
""".strip()


def build_scorer_user_prompt(
    profile: ProfileData,
    request: GenerateRequest,
    draft: ResumeDraft,
    generator_attempt: int,
    scorer_attempt: int,
    compile_feedback: str | None = None,
) -> str:
    draft_json = json.dumps(draft.model_dump(), indent=2, ensure_ascii=False)
    compile_notes = compile_feedback or "None"
    return f"""
Score this current resume draft.

Generator attempt: {generator_attempt}
Scorer attempt: {scorer_attempt}
Role focus: {request.role_focus or "not specified"}

Job description:
{request.job_description}

Canonical profile:
{profile.master_profile}

Project bank:
{json.dumps(profile.projects, indent=2, ensure_ascii=False)}

Skills bank:
{json.dumps(profile.skills, indent=2, ensure_ascii=False)}

Writing rules:
{profile.rules}

Compile feedback:
{compile_notes}

Current draft JSON:
{draft_json}

Scoring rubric:
- quality_score: overall CV quality from 0-100.
- match_score: role match from 0-100.
- Use stricter scoring when bullets are vague, the role target is unclear, keywords are missing, or the draft looks hard to fit on one page.
- Check the `profile` field against any configured personalized required phrase. If missing, add a gap and recommendation, lower `quality_score`, set `decision=revise`, and tell `gen` to integrate it naturally.
- Check the JSON draft against any configured personalized forbidden-content rule. If present, require removal because that content is handled by local configuration or should not appear in generated JSON.
- Use `decision=approve` only if the draft is ATS-safe, tightly targeted, credible, concise, and materially ready to send.
- recommendations and revision_brief must be specific enough for `gen` to act on immediately.
""".strip()
