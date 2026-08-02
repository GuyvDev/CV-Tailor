from __future__ import annotations

import unittest
from pathlib import Path

from app.cover_letter_renderer import (
    compact_cover_letter,
    cover_letter_company_edit,
    infer_cover_letter_company,
    is_header_only_cover_letter_edit,
    render_cover_letter,
    split_cover_letter,
)


class CoverLetterRendererTests(unittest.TestCase):
    def test_split_removes_model_signature(self) -> None:
        salutation, paragraphs = split_cover_letter(
            "Dear Hiring Team,\n\nOpening paragraph.\n\nSincerely,\nPrivate Name"
        )
        self.assertEqual(salutation, "Dear Hiring Team,")
        self.assertEqual(paragraphs, ["Opening paragraph."])

    def test_compaction_preserves_opening_and_conclusion(self) -> None:
        opening = " ".join(f"opening{i}" for i in range(150)) + "."
        middle = " ".join(f"middle{i}" for i in range(100)) + "."
        conclusion = "I welcome the opportunity to discuss the role."
        compacted = compact_cover_letter(
            f"Dear Hiring Team,\n\n{opening}\n\n{middle}\n\n{conclusion}\n\nSincerely,",
            max_words=200,
        )
        _, paragraphs = split_cover_letter(compacted)
        self.assertLessEqual(sum(len(item.split()) for item in paragraphs), 200)
        self.assertIn("opening0", compacted)
        self.assertIn(conclusion, compacted)

    def test_company_inference_avoids_job_board_name(self) -> None:
        company = infer_cover_letter_company(
            "Software Engineer\n\nAbout Example Labs\nWe build reliable systems.",
            "https://boards.greenhouse.io/examplelabs/jobs/123",
            "job-boards",
        )
        self.assertEqual(company, "Example Labs")

    def test_company_only_edit_does_not_require_regeneration(self) -> None:
        instructions = "Change the company in the left sidebar to Example Labs"
        company = cover_letter_company_edit(instructions)
        self.assertEqual(company, "Example Labs")
        self.assertTrue(is_header_only_cover_letter_edit(instructions, company))

    def test_public_template_is_placeholder_only_and_justified(self) -> None:
        template = Path("data/profile.example/templates/cover_letter.typ").read_text(
            encoding="utf-8-sig"
        )
        rendered = render_cover_letter(
            template,
            "Dear Hiring Team,\n\nA concise verified paragraph.\n\nSincerely,",
            {
                "name": "Example Candidate",
                "email": "candidate@example.com",
                "phone": "000-0000000",
                "location": "Example City",
            },
            portrait_filename=None,
            company="Example Labs",
        )
        self.assertIn("justify: true", rendered)
        self.assertIn("measure(letter-body)", rendered)
        self.assertNotIn("{{BODY}}", rendered)
        self.assertNotIn("{{NAME}}", rendered)


if __name__ == "__main__":
    unittest.main()
