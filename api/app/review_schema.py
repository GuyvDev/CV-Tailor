from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResumeScoreReport(BaseModel):
    quality_score: int = Field(ge=0, le=100)
    match_score: int = Field(ge=0, le=100)
    score_band: str
    decision: Literal["approve", "revise"]
    summary: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    revision_brief: str


SCORE_REPORT_SCHEMA = {
    "name": "resume_score_report",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "quality_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Overall CV quality score using ATS-safe, clarity, impact, and credibility criteria.",
            },
            "match_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Match score against the target role and job description.",
            },
            "score_band": {
                "type": "string",
                "description": "Short label such as Excellent, Strong, Good, Moderate, or Weak.",
            },
            "decision": {
                "type": "string",
                "enum": ["approve", "revise"],
                "description": "Approve only when the draft is strong, ATS-safe, and ready to ship without another rewrite.",
            },
            "summary": {
                "type": "string",
                "description": "Two-sentence summary of strengths, weaknesses, and recruiter-readiness.",
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top strengths already present in the draft.",
            },
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Biggest issues that lower CV quality or role match.",
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete ways to improve the CV without inventing facts.",
            },
            "revision_brief": {
                "type": "string",
                "description": "Compact rewrite instructions for the generator agent's next attempt.",
            },
        },
        "required": [
            "quality_score",
            "match_score",
            "score_band",
            "decision",
            "summary",
            "strengths",
            "gaps",
            "recommendations",
            "revision_brief",
        ],
    },
}
