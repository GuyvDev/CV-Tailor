from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StyleReference:
    name: str
    profile: str
    project_style: str


@dataclass(frozen=True)
class PersonalizationSettings:
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

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalizationSettings":
        return cls(
            required_profile_phrase=str(data.get("required_profile_phrase", "")),
            required_profile_recommendation=str(data.get("required_profile_recommendation", "")),
            forbidden_content_regex=str(data.get("forbidden_content_regex", "")),
            forbidden_content_gap=str(data.get("forbidden_content_gap", "")),
            forbidden_content_recommendation=str(data.get("forbidden_content_recommendation", "")),
            fixed_education_typst=str(data.get("fixed_education_typst", "")),
            contact_header_typst=str(data.get("contact_header_typst", "")),
            layout_density=str(data.get("layout_density", "compact")),
            default_profile_text=str(data.get("default_profile_text", "")),
            extra_prompt_notes=str(data.get("extra_prompt_notes", "")),
        )

    def model_dump(self) -> dict[str, str]:
        return {
            "required_profile_phrase": self.required_profile_phrase,
            "required_profile_recommendation": self.required_profile_recommendation,
            "forbidden_content_regex": self.forbidden_content_regex,
            "forbidden_content_gap": self.forbidden_content_gap,
            "forbidden_content_recommendation": self.forbidden_content_recommendation,
            "fixed_education_typst": self.fixed_education_typst,
            "contact_header_typst": self.contact_header_typst,
            "layout_density": self.layout_density,
            "default_profile_text": self.default_profile_text,
            "extra_prompt_notes": self.extra_prompt_notes,
        }


@dataclass(frozen=True)
class ProfileData:
    master_profile: str
    projects: list[dict]
    skills: dict[str, list[str]]
    rules: str
    cv_guidance: str
    template: str
    examples: dict[str, str]
    style_references: list[StyleReference]
    personalization: PersonalizationSettings


CONTACT_FIELD_RE = re.compile(r"^\*\s*(?P<key>[^:]+):\s*(?P<value>.+?)\s*$")


def _extract_section(text: str, start_heading: str, end_headings: tuple[str, ...]) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    collecting = False
    section_lines: list[str] = []
    start_heading = start_heading.upper()
    end_headings = tuple(item.upper() for item in end_headings)

    for raw_line in lines:
        line = raw_line.strip()
        upper_line = line.upper()
        if not collecting and upper_line == start_heading:
            collecting = True
            continue
        if collecting and upper_line in end_headings:
            break
        if collecting and line:
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def _load_style_references(profile_dir: Path) -> list[StyleReference]:
    extracted_dir = profile_dir.parent / "now" / "extracted_text"
    references: list[StyleReference] = []
    for path in sorted(extracted_dir.glob("CV-*.pdf.txt")):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        profile = _extract_section(text, "PROFILE", ("EDUCATION",))
        project_style = _extract_section(
            text,
            "TECHNICAL EXPERIENCE - AI & DATA ENGINEERING",
            ("SKILLS", "MILITARY SERVICE & VOLUNTEER WORK"),
        )
        references.append(
            StyleReference(
                name=path.name,
                profile=profile,
                project_style=project_style,
            )
        )
    return references


def _read_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()



def empty_personalization() -> PersonalizationSettings:
    return PersonalizationSettings()


def _load_personalization(profile_dir: Path) -> PersonalizationSettings:
    path = profile_dir / "personalization.json"
    if path.exists():
        return PersonalizationSettings.from_dict(json.loads(path.read_text(encoding="utf-8")))

    return empty_personalization()


def _load_cv_guidance(profile_dir: Path) -> str:
    guidance_parts = [
        _read_if_exists(profile_dir / "research_guidelines.md"),
        _read_if_exists(profile_dir / "notes" / "scanner_best_practices.md"),
    ]
    return "\n\n".join(part for part in guidance_parts if part)


def _active_projects(projects: list[dict]) -> list[dict]:
    return [
        project
        for project in projects
        if str(project.get("cv_status", "")).lower() not in {"hold", "on_hold", "paused"}
    ]


def load_profile_data(profile_dir: Path) -> ProfileData:
    examples_dir = profile_dir / "examples"
    examples = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(examples_dir.glob("*.md"))
    }
    projects = json.loads((profile_dir / "projects.json").read_text(encoding="utf-8"))

    return ProfileData(
        master_profile=(profile_dir / "master_profile.md").read_text(encoding="utf-8"),
        projects=_active_projects(projects),
        skills=json.loads((profile_dir / "skills.json").read_text(encoding="utf-8")),
        rules=(profile_dir / "rules.md").read_text(encoding="utf-8"),
        cv_guidance=_load_cv_guidance(profile_dir),
        template=(profile_dir / "templates" / "base_resume.typ").read_text(encoding="utf-8"),
        examples=examples,
        style_references=_load_style_references(profile_dir),
        personalization=_load_personalization(profile_dir),
    )


def extract_contact_fields(master_profile: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in master_profile.splitlines():
        match = CONTACT_FIELD_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key").strip().lower()
        result[key] = match.group("value").strip()
    return result


def extract_education_fields(master_profile: str) -> dict[str, str | list[str]]:
    lines = [line.strip() for line in master_profile.splitlines()]
    education_index = None
    for index, line in enumerate(lines):
        if line.lower() == "## education":
            education_index = index
            break

    school = degree = dates = ""
    bullets: list[str] = []
    if education_index is not None:
        for line in lines[education_index + 1 :]:
            if line.startswith("## "):
                break
            if not line.startswith("* "):
                continue
            value = line.removeprefix("* ").strip()
            if not school:
                school = value
            elif not degree:
                degree = value
            elif not dates:
                dates = value
            else:
                bullets.append(value)

    return {
        "school": school,
        "degree": degree,
        "dates": dates,
        "bullets": bullets,
    }
