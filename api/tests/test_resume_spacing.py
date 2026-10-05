from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.data_loader import PersonalizationSettings, load_profile_data
from app.renderer import apply_layout_density, render_resume
from app.resume_schema import ResumeDraft

try:
    import fitz
except ImportError:
    fitz = None


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "api/app/resume_template.typ").read_text(encoding="utf-8")
TYPST = os.getenv("TYPST_BIN") or shutil.which("typst")
if not TYPST and os.access(ROOT / ".tools/bin/typst", os.X_OK):
    TYPST = str(ROOT / ".tools/bin/typst")
if os.getenv("REQUIRE_RESUME_LAYOUT_TESTS") == "1" and not (TYPST and fitz):
    raise RuntimeError("PDF layout tests are required; install Typst and api/tests/requirements.txt")


def example_draft() -> ResumeDraft:
    return ResumeDraft.model_validate_json((ROOT / "api/tests/fixtures/resume_layout.json").read_text())


class ResumeSpacingTests(unittest.TestCase):
    def test_public_and_packaged_defaults_match(self) -> None:
        self.assertEqual(TEMPLATE, (ROOT / "data/profile.example/templates/base_resume.typ").read_text())

    def test_serverless_spacing_matches_packaged_default(self) -> None:
        generated = (ROOT / "frontend/api/stateless/resume-template.ts").read_text()
        literal = generated.split("export const DEFAULT_RESUME_TEMPLATE = ", 1)[1].strip().removesuffix(";")
        self.assertEqual(json.loads(literal), TEMPLATE)

    def test_default_renderer_needs_no_template_configuration(self) -> None:
        source = render_resume(None, example_draft())
        self.assertEqual(source, render_resume(TEMPLATE, example_draft()))
        self.assertIn('#let resume-density = "comfortable"', source)

    def test_profile_without_a_template_uses_the_packaged_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, content in {"master_profile.md": "Example profile.", "projects.json": "[]", "skills.json": "{}", "rules.md": "Use verified facts."}.items():
                (root / filename).write_text(content)
            self.assertEqual(load_profile_data(root).template, TEMPLATE)

    def test_presets_use_blocks_without_manual_spacers(self) -> None:
        for density in ("compact", "comfortable", "spacious"):
            with self.subTest(density=density):
                source = render_resume(TEMPLATE, example_draft(), PersonalizationSettings(layout_density=density))
                self.assertIn(f'#let resume-density = "{density}"', source)
                self.assertNotIn("{{", source)
                self.assertNotIn("#v(", source)
                self.assertEqual(source.count("#project(first: true)["), 1)

    def test_unknown_density_falls_back_to_compact(self) -> None:
        source = apply_layout_density(TEMPLATE, PersonalizationSettings(layout_density='unknown"'))
        self.assertIn('#let resume-density = "compact"', source)

    def test_density_is_case_insensitive(self) -> None:
        source = apply_layout_density(TEMPLATE, PersonalizationSettings(layout_density=" Comfortable "))
        self.assertIn('#let resume-density = "comfortable"', source)

    def test_legacy_template_keeps_its_existing_spacing(self) -> None:
        legacy = "{{PROFILE_SECTION}}\n#v(1fr)\n{{BODY_CONTENT}}"
        source = render_resume(legacy, example_draft(), PersonalizationSettings(layout_density="comfortable"))
        self.assertIn("#v(10pt)", source)
        self.assertIn("#v(0.45em)", source)
        self.assertNotIn("first: true", source)
        spacious = render_resume(legacy, example_draft(), PersonalizationSettings(layout_density="spacious"))
        self.assertIn("#v(1fr)", spacious)

    def test_empty_project_list_renders(self) -> None:
        draft = example_draft().model_copy(update={"projects": []})
        source = render_resume(TEMPLATE, draft)
        self.assertNotIn("#project(first: true)[", source)
        self.assertNotIn("{{", source)
        self.assertNotIn('#section("Projects")', source)

    def test_empty_sections_and_filtered_projects_have_no_orphan_headings(self) -> None:
        draft = example_draft().model_copy(deep=True)
        draft.profile = ""
        draft.education.school = draft.education.degree = draft.education.dates = ""
        draft.education.bullets = []
        draft.projects[0].bullets = [" "]
        draft.skills = []
        source = render_resume(None, draft)
        self.assertNotIn('#section("Profile")', source)
        self.assertNotIn('#section("Education")', source)
        self.assertNotIn('#section("Skills")', source)
        self.assertNotIn(draft.projects[0].title, source)
        self.assertIn('#project(first: true)[Operating Systems Laboratory', source)


@unittest.skipUnless(TYPST and fitz, "Install Typst and api/tests/requirements.txt for PDF geometry tests")
class ResumePdfSpacingTests(unittest.TestCase):
    def compile(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "resume.typ"
            pdf_path = Path(directory) / "resume.pdf"
            source_path.write_text(source, encoding="utf-8")
            result = subprocess.run([TYPST, "compile", str(source_path), str(pdf_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return fitz.open(stream=pdf_path.read_bytes(), filetype="pdf")

    def lines(self, page):
        return sorted([
            ("".join(span["text"] for span in line["spans"]).strip(), line["bbox"])
            for block in page.get_text("dict")["blocks"] if "lines" in block
            for line in block["lines"]
            if "".join(span["text"] for span in line["spans"]).strip() not in {"", "•"}
        ], key=lambda item: (item[1][1], item[1][0]))

    def test_dense_resume_fits_one_page_at_each_density(self) -> None:
        for density in ("compact", "comfortable", "spacious"):
            with self.subTest(density=density):
                source = render_resume(TEMPLATE, example_draft(), PersonalizationSettings(layout_density=density))
                with self.compile(source) as pdf:
                    self.assertEqual(len(pdf), 1)
                    page = pdf[0]
                    margin = 2 / 2.54 * 72
                    for _, bounds in self.lines(page):
                        self.assertGreaterEqual(bounds[1], margin - 0.1)
                        self.assertLessEqual(bounds[3], page.rect.height - margin + 0.1)

    def test_section_gaps_are_consistent_and_closer_to_content(self) -> None:
        for density in ("compact", "comfortable", "spacious"):
            with self.subTest(density=density):
                source = render_resume(TEMPLATE, example_draft(), PersonalizationSettings(layout_density=density))
                with self.compile(source) as pdf:
                    lines = self.lines(pdf[0])
                    above, below = [], []
                    for index, (text, bounds) in enumerate(lines):
                        if text not in {"EDUCATION", "PROJECTS", "SKILLS"}:
                            continue
                        above.append(bounds[1] - lines[index - 1][1][3])
                        below.append(lines[index + 1][1][1] - bounds[3])
                        self.assertGreater(above[-1], below[-1] + 1)
                    self.assertEqual(len(above), 3)
                    self.assertLess(max(above) - min(above), 0.05)
                    self.assertLess(max(below) - min(below), 0.05)

    def test_project_titles_are_closer_to_their_bullets(self) -> None:
        source = render_resume(TEMPLATE, example_draft(), PersonalizationSettings(layout_density="spacious"))
        with self.compile(source) as pdf:
            lines = self.lines(pdf[0])
            gaps = []
            for index, (text, bounds) in enumerate(lines):
                if not text.startswith(("Operating Systems Laboratory |", "Accelerated Image Processing |")):
                    continue
                above = bounds[1] - lines[index - 1][1][3]
                below = lines[index + 1][1][1] - bounds[3]
                self.assertGreater(above, below + 2)
                gaps.append(above)
            self.assertEqual(len(gaps), 2)
            self.assertAlmostEqual(*gaps, delta=0.05)

    def test_wrapped_lines_and_adjacent_bullets_have_equal_spacing(self) -> None:
        layout = apply_layout_density(TEMPLATE.split("// Header")[0].replace("#show: resume-page", ""), PersonalizationSettings())
        source = layout + '\n#bullet[' + "Verified technical evidence and repeatable debugging workflow. " * 3 + ']\n#bullet[Following evidence.]'
        with self.compile(source) as pdf:
            lines = self.lines(pdf[0])
            self.assertGreaterEqual(len(lines), 3)
            steps = [after[1][1] - before[1][1] for before, after in zip(lines, lines[1:])]
            self.assertLess(max(steps) - min(steps), 0.05)

    def test_profile_and_academic_notes_share_line_spacing(self) -> None:
        notes = '''
#profile-note[#strong[M.Sc. in Engineering with a specialization in computing]]
#profile-note[#strong[Specialization semester averages: 91, 88, 94]]
'''
        for density in ("compact", "comfortable", "spacious"):
            for width in (468, 250):
                with self.subTest(density=density, width=width):
                    layout = apply_layout_density(TEMPLATE.split("// Header")[0].replace("#show: resume-page", ""), PersonalizationSettings(layout_density=density))
                    source = layout + f'\n#set page(width: {width + 48}pt, margin: 24pt)\n#show: resume-page\n'
                    source += '#section("Profile")\n' + "Verified engineering experience with repeatable debugging and systems validation. " * 3 + '\n' + notes
                    with self.compile(source) as pdf:
                        self.assertEqual(len(pdf), 1)
                        lines = [line for block in pdf[0].get_text("dict")["blocks"] if "lines" in block
                                 for line in block["lines"] if line["spans"]]
                        lines.sort(key=lambda line: line["spans"][0]["origin"][1])
                        lines = [line for line in lines if "".join(span["text"] for span in line["spans"]) != "PROFILE"]
                        texts = ["".join(span["text"] for span in line["spans"]) for line in lines]
                        self.assertTrue(any(text.startswith("M.Sc.") for text in texts))
                        self.assertTrue(any(text.startswith("Specialization") for text in texts))
                        if width == 250:
                            self.assertTrue(any("computing" in text and not text.startswith("M.Sc.") for text in texts))
                        baselines = [line["spans"][0]["origin"][1] for line in lines]
                        steps = [after - before for before, after in zip(baselines, baselines[1:])]
                        self.assertGreaterEqual(len(steps), 3)
                        self.assertLess(max(steps) - min(steps), 0.05)

    def test_headings_stay_with_first_bullet_at_page_boundary(self) -> None:
        layout = apply_layout_density(TEMPLATE.split("// Header")[0].replace("#show: resume-page", ""), PersonalizationSettings())
        source = layout + '''
#set page(width: 360pt, height: 150pt, margin: 12pt)
#block(height: 90pt, breakable: false)[]
#section("Boundary section")
#project(first: true)[Boundary project]
#bullet[First evidence remains with both headings.]
'''
        with self.compile(source) as pdf:
            self.assertEqual(len(pdf), 2)
            self.assertNotIn("Boundary", pdf[0].get_text())
            self.assertIn("BOUNDARY SECTION", pdf[1].get_text())
            self.assertIn("Boundary project", pdf[1].get_text())
            self.assertIn("First evidence", pdf[1].get_text())

    def test_wrapped_project_title_and_bullet_stay_together(self) -> None:
        layout = apply_layout_density(TEMPLATE.split("// Header")[0].replace("#show: resume-page", ""), PersonalizationSettings())
        source = layout + '''
#set page(width: 220pt, height: 150pt, margin: 12pt)
#block(height: 95pt, breakable: false)[]
#project[Long project heading with timing-sensitive interfaces and concurrency]
#bullet[First evidence with a wrapped continuation for the project.]
'''
        with self.compile(source) as pdf:
            self.assertEqual(len(pdf), 2)
            first_page = " ".join(pdf[0].get_text().split())
            second_page = " ".join(pdf[1].get_text().split())
            self.assertNotIn("Long project", first_page)
            self.assertIn("Long project", second_page)
            self.assertIn("First evidence", second_page)

    def test_editable_master_template_compiles(self) -> None:
        master = (ROOT / "data/profile.example/templates/master.typ").read_text()
        with self.compile(render_resume(master, example_draft())) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_short_cv_stretch_is_bounded_and_keeps_heading_proximity(self) -> None:
        full = example_draft()
        short = full.model_copy(deep=True)
        short.projects = short.projects[:1]
        short.projects[0].bullets = ["Built and verified the system."]
        short.skills = short.skills[:1]
        for density in ("compact", "comfortable", "spacious"):
            for draft in (short, full):
                source = render_resume(None, draft, PersonalizationSettings(layout_density=density))
                steps = []
                for candidate in (source.replace("#show: resume-page", ""), source):
                    with self.compile(candidate) as pdf:
                        self.assertEqual(len(pdf), 1)
                        lines = self.lines(pdf[0])
                        index = next(i for i, (text, _) in enumerate(lines) if text == "PROJECTS")
                        above = lines[index][1][1] - lines[index - 1][1][3]
                        below = lines[index + 1][1][1] - lines[index][1][3]
                        self.assertGreater(above, below + 1)
                        profile_index = next(i for i, (text, _) in enumerate(lines) if text == "PROFILE")
                        steps.append(lines[profile_index + 2][1][1] - lines[profile_index + 1][1][1])
                leading = {"compact": 4.5, "comfortable": 5, "spacious": 5.5}[density]
                factor = 1 + (steps[1] - steps[0]) / leading
                self.assertGreaterEqual(factor, 1)
                self.assertLessEqual(factor, 1.6 + 0.001)

    def test_stretch_fills_available_height_without_changing_text_or_fonts(self) -> None:
        source = render_resume(None, example_draft(), PersonalizationSettings(layout_density="spacious"))
        with self.compile(source.replace("#show: resume-page", "")) as original, self.compile(source) as stretched:
            self.assertEqual(len(stretched), 1)
            self.assertEqual(" ".join(original[0].get_text().split()), " ".join(stretched[0].get_text().split()))
            margin = 2 / 2.54 * 72
            lines = self.lines(stretched[0])
            self.assertAlmostEqual(lines[0][1][1], margin, delta=0.1)
            self.assertAlmostEqual(lines[-1][1][3], stretched[0].rect.height - margin, delta=1)
            self.assertGreater(lines[-1][1][3], self.lines(original[0])[-1][1][3] + 100)
            def font_sizes(page):
                return [span["size"] for block in page.get_text("dict")["blocks"] if "lines" in block
                        for line in block["lines"] for span in line["spans"]]
            self.assertEqual(font_sizes(original[0]), font_sizes(stretched[0]))

    def test_stretch_is_deterministic_and_leaves_overflow_spacing_alone(self) -> None:
        source = render_resume(None, example_draft())
        with self.compile(source) as first, self.compile(source) as second:
            self.assertEqual(self.lines(first[0]), self.lines(second[0]))
        layout = apply_layout_density(TEMPLATE.split("// Header")[0].replace("#show: resume-page", ""), PersonalizationSettings())
        body = '\n#section("Evidence")\n#bullet[' + "Verified systems evidence and repeatable debugging. " * 200 + ']'
        with self.compile(layout + body) as original, self.compile(layout + '\n#show: resume-page\n' + body) as stretched:
            self.assertGreater(len(stretched), 1)
            self.assertEqual(len(original), len(stretched))
            for before, after in zip(original, stretched):
                self.assertEqual(self.lines(before), self.lines(after))

    def test_oversized_bullet_flows_without_clipping_content(self) -> None:
        layout = apply_layout_density(TEMPLATE.split("// Header")[0].replace("#show: resume-page", ""), PersonalizationSettings())
        source = layout + '\n#section("Long evidence")\n#bullet[' + "Verified technical evidence and repeatable debugging workflow. " * 200 + 'Final evidence marker.]'
        with self.compile(source) as pdf:
            self.assertGreater(len(pdf), 1)
            self.assertIn("Final evidence marker.", " ".join(pdf[-1].get_text().split()))
            margin = 2 / 2.54 * 72
            for page in pdf:
                for _, bounds in self.lines(page):
                    self.assertGreaterEqual(bounds[1], margin - 0.1)
                    self.assertLessEqual(bounds[3], page.rect.height - margin + 0.1)

    def test_real_compiler_fits_dense_content_with_spacing_alone(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock
        from app.models import CompileResponse
        from app.services.compiler_client import CompilerClient

        draft = example_draft().model_copy(deep=True)
        draft.profile += " Investigated timing-sensitive failures and verified the behavior through repeatable testing." * 12
        source = render_resume(None, draft, PersonalizationSettings(layout_density="spacious"))

        async def compile_locally(candidate):
            with self.compile(candidate) as pdf:
                return CompileResponse(success=True, page_count=len(pdf))

        client = CompilerClient("http://unused.invalid")
        client.compile = AsyncMock(side_effect=compile_locally)
        selected, result = asyncio.run(client.compile_resume(source))
        self.assertEqual(result.page_count, 1)
        self.assertGreater(client.compile.await_count, 1)
        density_rule = r'^#let resume-density = ".+"$'
        self.assertEqual(
            re.sub(density_rule, "", selected, flags=re.MULTILINE),
            re.sub(density_rule, "", source, flags=re.MULTILINE),
        )


if __name__ == "__main__":
    unittest.main()
