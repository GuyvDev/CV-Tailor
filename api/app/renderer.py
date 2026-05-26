from __future__ import annotations

import re

from app.data_loader import PersonalizationSettings, empty_personalization
from app.resume_schema import ProjectEntry, ResumeDraft, SkillBucket

INLINE_COMMANDS = {
    "#strong[": "strong",
    "#emph[": "emph",
}

def escape_typst(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("#", "\\#")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("@", "\\@")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace("*", "\\*")
        .replace("_", "\\_")
    )


def _find_closing_bracket(value: str, start: int) -> int:
    depth = 1
    for index in range(start, len(value)):
        if value[index] == "[":
            depth += 1
        elif value[index] == "]":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _render_wrapped_markup(value: str, token: str, command: str, start: int) -> tuple[str, int] | None:
    if not value.startswith(token, start):
        return None
    end = value.find(token, start + len(token))
    if end == -1 or end == start + len(token):
        return None
    inner = value[start + len(token):end]
    return f"#{command}[{render_inline_markup(inner)}]", end + len(token)


def render_inline_markup(value: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(value):
        matched_command = False
        for prefix, command in INLINE_COMMANDS.items():
            if value.startswith(prefix, index):
                end = _find_closing_bracket(value, index + len(prefix))
                if end != -1:
                    inner = value[index + len(prefix):end]
                    parts.append(f"#{command}[{render_inline_markup(inner)}]")
                    index = end + 1
                    matched_command = True
                    break
        if matched_command:
            continue

        for token, command in (("**", "strong"), ("__", "strong"), ("*", "emph"), ("_", "emph")):
            rendered = _render_wrapped_markup(value, token, command, index)
            if rendered is not None:
                chunk, index = rendered
                parts.append(chunk)
                break
        else:
            parts.append(escape_typst(value[index]))
            index += 1
    return "".join(parts)


def render_contact_header(resume: ResumeDraft, personalization: PersonalizationSettings) -> str:
    contact = resume.contact
    linkedin_url = contact.linkedin if contact.linkedin.startswith("http") else f"https://{contact.linkedin}" if contact.linkedin else ""
    github_url = contact.github if contact.github.startswith("http") else f"https://{contact.github}" if contact.github else ""
    linkedin_label = contact.linkedin.replace("https://", "").replace("http://", "")
    github_label = contact.github.replace("https://", "").replace("http://", "")
    if personalization.contact_header_typst.strip():
        values = {
            "{{NAME}}": escape_typst(contact.name or "Candidate Name"),
            "{{LOCATION}}": escape_typst(contact.location or ""),
            "{{EMAIL}}": escape_typst(contact.email or ""),
            "{{LINKEDIN}}": escape_typst(linkedin_label),
            "{{LINKEDIN_URL}}": escape_typst(linkedin_url),
            "{{GITHUB}}": escape_typst(github_label),
            "{{GITHUB_URL}}": escape_typst(github_url),
        }
        header = personalization.contact_header_typst.replace("\\n", "\n")
        for key, value in values.items():
            header = header.replace(key, value)
        return header

    location = escape_typst(contact.location) if contact.location else ""
    email = escape_typst(contact.email) if contact.email else ""
    details = " | ".join(part for part in (location, email) if part)
    lines = [
        '#align(center)[',
        f'  #text(fill: theme-blue, weight: "bold", size: 14pt)[{escape_typst(contact.name or "Candidate Name")}]',
    ]
    if details:
        lines.append(f'  #text(fill: black)[ | {details} |]')
        lines.append('  #linebreak()')
    links: list[str] = []
    if contact.linkedin:
        links.append(f'#link("{escape_typst(linkedin_url)}")[#text(fill: theme-blue)[{escape_typst(linkedin_label)}]]')
    if contact.github:
        links.append(f'#link("{escape_typst(github_url)}")[#text(fill: theme-blue)[{escape_typst(github_label)}]]')
    if links:
        lines.append('  ' + ' #text[ | ] '.join(links))
    lines.extend([
        '  #v(-4pt)',
        '  #line(length: 100%, stroke: 0.5pt + gray)',
        ']',
    ])
    return "\n".join(lines)


def normalize_profile_markup(value: str, personalization: PersonalizationSettings) -> str:
    required_phrase = personalization.required_profile_phrase.strip()
    if not required_phrase:
        return value
    plain_phrase = re.sub(r"#(?:strong|emph)\[(.*?)\]", r"\1", required_phrase)
    strong_phrase = (
        required_phrase
        if required_phrase.startswith("#strong[")
        else f"#strong[{plain_phrase}]"
    )
    patterns = (
        rf"#emph\[\s*{re.escape(plain_phrase)}\s*\]",
        rf"\*\*\s*{re.escape(plain_phrase)}\s*\*\*",
        rf"__\s*{re.escape(plain_phrase)}\s*__",
        rf"\*\s*{re.escape(plain_phrase)}\s*\*",
        rf"_\s*{re.escape(plain_phrase)}\s*_",
    )
    normalized = value
    for pattern in patterns:
        normalized = re.sub(pattern, strong_phrase, normalized, flags=re.IGNORECASE)
    if strong_phrase.lower() in normalized.lower():
        return normalized
    return re.sub(re.escape(plain_phrase), strong_phrase, normalized, flags=re.IGNORECASE)


def strip_generated_semester_averages(value: str, personalization: PersonalizationSettings) -> str:
    pattern = personalization.forbidden_content_regex.strip()
    if not pattern:
        return value.strip()
    stripped = re.sub(pattern, "", value, flags=re.IGNORECASE)
    stripped = re.sub(r"\s+([,.;:])", r"\1", stripped)
    stripped = re.sub(r"^\s*[;|,.-]+\s*", "", stripped)
    stripped = re.sub(r"\s*[;|,.-]+\s*$", "", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped)
    return stripped.strip()


def _first_year(dates: str) -> str:
    m = re.search(r"\d{4}", dates)
    return m.group() if m else dates


def render_project(project: ProjectEntry, personalization: PersonalizationSettings) -> str:
    stack_str = " / ".join(project.stack[:2]) if project.stack else ""
    year = _first_year(project.dates) if project.dates else ""

    parts = [project.title]
    if stack_str:
        parts.append(stack_str)
    if year:
        parts.append(year)
    title_line = " | ".join(parts)

    bullets = "\n".join(
        f"#bullet[{render_inline_markup(clean_item)}]"
        for item in project.bullets
        if (clean_item := strip_generated_semester_averages(item, personalization))
    )
    return f"#project[{escape_typst(title_line)}]\n{bullets}"


def render_skills(skills: list[SkillBucket], personalization: PersonalizationSettings) -> str:
    lines: list[str] = []
    for bucket in skills:
        items = ", ".join(
            render_inline_markup(clean_item)
            for item in bucket.items
            if (clean_item := strip_generated_semester_averages(item, personalization))
        )
        if not items:
            continue
        lines.append(f"#bullet[#strong[{escape_typst(bucket.category)}:] {items}]")
    return "\n".join(lines)


def layout_gap(personalization: PersonalizationSettings) -> str:
    density = personalization.layout_density.strip().lower()
    if density == "spacious":
        return "#v(1fr)"
    if density == "comfortable":
        return "#v(10pt)"
    return "#v(6pt)"


def apply_layout_density(source: str, personalization: PersonalizationSettings) -> str:
    density = personalization.layout_density.strip().lower()
    if density in {"compact", "comfortable"}:
        return source.replace("#v(1fr)", layout_gap(personalization))
    return source


def render_resume(template: str, resume: ResumeDraft, personalization: PersonalizationSettings | None = None) -> str:
    personalization = personalization or empty_personalization()

    # ── profile ───────────────────────────────────────────────────────────────
    profile_section = (
        f'#section("Profile")\n'
        f'{render_inline_markup(normalize_profile_markup(strip_generated_semester_averages(resume.profile, personalization), personalization))}'
    )

    # ── education ─────────────────────────────────────────────────────────────
    if personalization.fixed_education_typst:
        edu_lines = personalization.fixed_education_typst.replace("\\n", "\n").splitlines()
    else:
        edu_header = " | ".join(
            part
            for part in (
                resume.education.school,
                resume.education.degree,
                f"#emph[{escape_typst(resume.education.dates)}]" if resume.education.dates else "",
            )
            if part
        )
        edu_lines = [render_inline_markup(edu_header)] if edu_header else []
    for b in resume.education.bullets:
        cleaned = strip_generated_semester_averages(b, personalization)
        if cleaned:
            edu_lines.append(f"#bullet[{render_inline_markup(cleaned)}]")
    education_section = '#section("Education")\n' + "\n".join(edu_lines)

    # ── projects ──────────────────────────────────────────────────────────────
    project_blocks = "\n#v(0.45em)\n".join(render_project(p, personalization) for p in resume.projects)
    projects_section = f'#section("Projects")\n{project_blocks}'

    # ── skills ────────────────────────────────────────────────────────────────
    skills_section = f'#section("Skills")\n{render_skills(resume.skills, personalization)}'

    # ── assemble remaining body sections ──────────────────────────────────────
    body_content = "\n\n#v(1fr)\n\n".join([
        education_section,
        projects_section,
        skills_section,
    ])

    rendered = (
        template
        .replace("{{CONTACT_HEADER}}", render_contact_header(resume, personalization))
        .replace("{{PROFILE_SECTION}}", profile_section)
        .replace("{{BODY_CONTENT}}", body_content)
    )
    return apply_layout_density(rendered, personalization)
