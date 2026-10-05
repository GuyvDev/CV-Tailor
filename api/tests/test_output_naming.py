from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.data_loader import load_profile_data
from app.models import CompileResponse, GenerateRequest
from app.output_naming import infer_output_company, short_output_company
from app.resume_schema import ResumeDraft
from app.review_schema import ResumeScoreReport
from app.services.job_store import JobStore


ROOT = Path(__file__).resolve().parents[2]
APPLE_URL = "https://jobs.apple.com/en-il/details/200676939-0865/software-engineering-student?team=HRDWR"
AMAZON_URL = "https://www.amazon.jobs/en/jobs/10568678/software-engineer-annapurna-labs-tel-aviv-haifa-annapurna-labs"
POSTINGS = (
    ("Apply for a Software Engineering Student job at Apple. Read about the role.", APPLE_URL, "Apple"),
    ("Annapurna Labs designs custom silicon for Amazon Web Services.\nAbout the team\nWhy Annapurna Labs", AMAZON_URL, "Amazon"),
)


class OutputCompanyTests(unittest.TestCase):
    def test_company_suffix_has_at_most_two_words(self) -> None:
        cases = {
            "The Computational Molecular Engineering": "Computational Molecular",
            "Example Research Laboratories Inc.": "Example Research",
            "Example_Research_Laboratories": "Example Research",
            "Example-Research-Laboratories": "Example Research",
            "Research & Development Labs": "Research Development",
            "P&G Research Labs": "P G",
            "Example.Co Research Labs": "Example Co",
            "Apple": "Apple",
            "Amazon": "Amazon",
            "": "",
        }
        for company, expected in cases.items():
            with self.subTest(company=company):
                self.assertEqual(short_output_company(company), expected)
                self.assertLessEqual(len(short_output_company(company).split()), 2)

    def test_reported_apple_and_amazon_postings(self) -> None:
        for description, url, employer in POSTINGS:
            with self.subTest(employer=employer):
                self.assertEqual(infer_output_company(description, url), employer)

    def test_explicit_employer_overrides_other_evidence(self) -> None:
        self.assertEqual(infer_output_company("About the team\nEmployer: Example Labs", APPLE_URL), "Example Labs")

    def test_visible_employer_is_preserved_for_job_boards(self) -> None:
        for description in ("Company: Example Labs", "About Example Labs", "Example Labs is hiring engineers.", "Software Engineer — Example Labs"):
            with self.subTest(description=description):
                self.assertEqual(infer_output_company(description, "https://www.comeet.com/jobs/123"), "Example Labs")

    def test_generic_headings_and_job_boards_are_not_employers(self) -> None:
        for url in ("https://www.comeet.com/jobs/123", "https://boards.greenhouse.io/jobs/123", "https://www.linkedin.com/jobs/123", "https://secrethunter.io/jobs/123", "https://example.myworkdayjobs.com/en-US/jobs/123"):
            with self.subTest(url=url):
                self.assertEqual(infer_output_company("Company: Comeet\nAbout the team\nAbout us", url), "")

    def test_first_party_domain_fallback(self) -> None:
        self.assertEqual(infer_output_company("Engineering role description.", "https://careers.example-labs.com/jobs/123"), "Example Labs")
        self.assertEqual(infer_output_company("Engineering role description.", "https://jobs.example.co.il/jobs/123"), "Example")

    def test_official_domain_matching_requires_domain_boundary(self) -> None:
        for url in ("https://apple.com.example.org/jobs/123", "https://notapple.com/jobs/123", "https://amazon.jobs.example.org/jobs/123"):
            with self.subTest(url=url):
                self.assertNotIn(infer_output_company("Engineering role description.", url), {"Apple", "Amazon"})

    def test_absent_or_malformed_url_and_unsafe_employer_fall_back(self) -> None:
        for url in ("", "not a url", "https://[invalid", "http://127.0.0.1/jobs/123"):
            with self.subTest(url=url):
                self.assertEqual(infer_output_company("Company: ../../unsafe/name\nAbout the role", url), "")


class OutputNamingPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Import the real API with temporary paths; never touch a local profile.
        cls.runtime = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.runtime.cleanup)
        with patch.dict(os.environ, {
            "OUTPUT_DIR": cls.runtime.name + "/outputs",
            "PROFILE_DIR": cls.runtime.name + "/data/profile",
        }):
            cls.api = importlib.import_module("app.main")

    def test_new_and_approved_cv_artifacts_follow_name_setting(self) -> None:
        draft = ResumeDraft.model_validate_json((ROOT / "api/tests/fixtures/resume_layout.json").read_text())
        profile = load_profile_data(ROOT / "data/profile.example")
        report = ResumeScoreReport(
            quality_score=90, match_score=90, score_band="Excellent", decision="approve",
            summary="Verified fit.", strengths=["Verified experience."], gaps=[],
            recommendations=[], revision_brief="Preserve verified facts.",
        )
        pdf_bytes = b"%PDF-test-artifact"

        async def compile_resume(source):
            return source, CompileResponse(success=True, page_count=1, pdf_base64=base64.b64encode(pdf_bytes).decode())

        pipeline_postings = (*POSTINGS, (
            "The Computational Molecular Engineering is seeking researchers.",
            "https://www.linkedin.com/jobs/view/4473484556", "Computational_Molecular",
        ))
        for description, url, employer in pipeline_postings:
            for approved in (False, True):
                for enabled in (False, True):
                    with self.subTest(employer=employer, approved=approved, enabled=enabled), tempfile.TemporaryDirectory() as directory:
                        store = JobStore(Path(directory))
                        job_id = "example-job"
                        store.create_job(job_id, None, "default")
                        generator = Mock()
                        generator.generate_resume.return_value = draft
                        generator.score_resume.return_value = report
                        payload = GenerateRequest(job_description=description, source_url=url, approved_draft=draft if approved else None)
                        with (
                            patch.object(self.api, "job_store", store),
                            patch.object(self.api, "load_profile_data", return_value=profile),
                            patch.object(self.api, "_read_app_settings_secret", return_value={"output_basename": "CV-Candidate", "append_company_to_output_name": enabled}),
                            patch.object(self.api.compiler_client, "compile_resume", AsyncMock(side_effect=compile_resume)),
                        ):
                            asyncio.run(self.api._run_generation_job_once(job_id, payload, generator, self.api.JobControl()))
                        metadata = store.load(job_id)
                        self.assertEqual(metadata.status, "completed", metadata.error)
                        name = "CV-Candidate" + (f"-{employer}" if enabled else "")
                        self.assertEqual(metadata.pdf_url, f"/files/{job_id}/{name}.pdf")
                        self.assertEqual(metadata.typst_url, f"/files/{job_id}/{name}.typ")
                        self.assertEqual((store.job_dir(job_id) / f"{name}.pdf").read_bytes(), pdf_bytes)
                        self.assertEqual((store.job_dir(job_id) / f"{name}.typ").read_text(), (store.job_dir(job_id) / "resume.typ").read_text())


if __name__ == "__main__":
    unittest.main()
