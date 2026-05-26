from __future__ import annotations

from pydantic import BaseModel, Field


class ContactInfo(BaseModel):
    name: str
    email: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""


class EducationEntry(BaseModel):
    school: str
    degree: str
    dates: str
    bullets: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    title: str
    stack: list[str] = Field(default_factory=list)
    dates: str = ""
    bullets: list[str] = Field(default_factory=list, min_length=1)


class SkillBucket(BaseModel):
    category: str
    items: list[str] = Field(default_factory=list, min_length=1)


class ResumeDraft(BaseModel):
    contact: ContactInfo
    headline: str
    profile: str
    education: EducationEntry
    projects: list[ProjectEntry] = Field(default_factory=list)
    skills: list[SkillBucket] = Field(default_factory=list)
    fit_summary: str
    keywords: list[str] = Field(default_factory=list)
    job_title: str = ""


RESUME_SCHEMA = {
    "name": "tailored_resume",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "contact": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "location": {"type": "string"},
                    "linkedin": {"type": "string"},
                    "github": {"type": "string"},
                },
                "required": ["name", "email", "location", "linkedin", "github"],
            },
            "headline": {
                "type": "string",
                "description": "Leave this as an empty string by default. Use a separate profile headline only when a human explicitly asks for one.",
            },
            "profile": {
                "type": "string",
                "description": "Concise 1-2 sentence summary using only verified facts and role-relevant keywords. Apply any private profile constraints supplied by the runtime configuration.",
            },
            "education": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "school": {"type": "string"},
                    "degree": {"type": "string"},
                    "dates": {"type": "string"},
                    "bullets": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["school", "degree", "dates", "bullets"],
            },
            "projects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "stack": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "dates": {"type": "string"},
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["title", "stack", "dates", "bullets"],
                },
            },
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "category": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["category", "items"],
                },
            },
            "fit_summary": {
                "type": "string",
                "description": "Brief explanation of why the selected evidence fits the target role.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
            },
            "job_title": {
                "type": "string",
                "description": "Exact job title from the posting when available.",
            },
        },
        "required": [
            "contact",
            "headline",
            "profile",
            "education",
            "projects",
            "skills",
            "fit_summary",
            "keywords",
            "job_title",
        ],
    },
}
