from __future__ import annotations

import unittest

from app.data_loader import PersonalizationSettings, ProfileData
from app.models import GenerateRequest
from app.prompting import build_generator_user_prompt
from app.resume_schema import ResumeDraft
from app.review_schema import ResumeScoreReport
from app.services.openai_service import apply_content_density_score


def profile_data() -> ProfileData:
    projects = [
        {
            "title": f"Project {index}",
            "stack": ["Python", "Docker"],
            "facts": [
                "Built the verified deliverable.",
                "Implemented the verified technical workflow.",
                "Validated the verified result.",
            ],
        }
        for index in range(1, 4)
    ]
    return ProfileData(
        master_profile="# Candidate Profile\n\n## Summary Facts\n* Verified engineering work.",
        projects=projects,
        skills={"languages": ["Python"], "systems": ["Linux"], "tools": ["Docker"]},
        rules="Use verified facts only.",
        cv_guidance="Prefer concrete project evidence.",
        template="{{CONTACT_HEADER}}{{PROFILE_SECTION}}{{BODY_CONTENT}}",
        examples={},
        style_references=[],
        personalization=PersonalizationSettings(),
    )


def draft(*, bullets_per_project: int, skill_categories: int) -> ResumeDraft:
    return ResumeDraft.model_validate(
        {
            "contact": {"name": "Candidate", "email": "", "location": "", "linkedin": "", "github": ""},
            "headline": "",
            "profile": "Engineer with verified technical project experience. Builds and validates practical systems.",
            "education": {"school": "University", "degree": "B.Sc.", "dates": "2026", "bullets": []},
            "projects": [
                {
                    "title": f"Project {index}",
                    "stack": ["Python", "Docker"],
                    "dates": "2026",
                    "bullets": [f"Distinct verified evidence {bullet}." for bullet in range(bullets_per_project)],
                }
                for index in range(1, 4)
            ],
            "skills": [
                {"category": f"Category {index}", "items": ["Verified skill"]}
                for index in range(skill_categories)
            ],
            "fit_summary": "Verified fit.",
            "keywords": ["Python"],
            "job_title": "Engineer",
        }
    )


def approved_report() -> ResumeScoreReport:
    return ResumeScoreReport(
        quality_score=90,
        match_score=90,
        score_band="Excellent",
        decision="approve",
        summary="Ready.",
        strengths=["Grounded."],
        gaps=[],
        recommendations=[],
        revision_brief="Preserve the strongest evidence.",
    )


class ResumeDensityTests(unittest.TestCase):
    def test_one_page_retry_is_told_to_enrich_not_compress(self) -> None:
        profile = profile_data()
        prompt = build_generator_user_prompt(
            profile,
            GenerateRequest(job_description="A sufficiently detailed engineering job description."),
            2,
            draft(bullets_per_project=1, skill_categories=2),
            approved_report(),
            "success=True, page_count=1, log=n/a",
        )
        self.assertIn("already compiled to one page", prompt)
        self.assertIn("Do not shorten it", prompt)
        self.assertIn("at least 8 project bullets", prompt)

    def test_multi_page_retry_still_requests_targeted_compression(self) -> None:
        prompt = build_generator_user_prompt(
            profile_data(),
            GenerateRequest(job_description="A sufficiently detailed engineering job description."),
            2,
            draft(bullets_per_project=3, skill_categories=3),
            approved_report(),
            "success=True, page_count=2, log=n/a",
        )
        self.assertIn("exceeded one page", prompt)
        self.assertIn("preserving the strongest profile", prompt)

    def test_sparse_supported_one_page_draft_cannot_be_approved(self) -> None:
        report = apply_content_density_score(
            approved_report(),
            draft(bullets_per_project=1, skill_categories=2),
            profile_data(),
            "success=True, page_count=1, log=n/a",
        )
        self.assertEqual(report.decision, "revise")
        self.assertLessEqual(report.quality_score, 76)
        self.assertIn("structurally sparse", report.gaps[-1])

    def test_evidence_rich_one_page_draft_keeps_approval(self) -> None:
        original = approved_report()
        report = apply_content_density_score(
            original,
            draft(bullets_per_project=3, skill_categories=3),
            profile_data(),
            "success=True, page_count=1, log=n/a",
        )
        self.assertEqual(report, original)


if __name__ == "__main__":
    unittest.main()
