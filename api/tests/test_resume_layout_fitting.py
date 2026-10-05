from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from app.models import CompileResponse
from app.renderer import render_resume
from app.services.compiler_client import CompilerClient
from test_resume_spacing import example_draft


class ResumeLayoutFittingTests(unittest.IsolatedAsyncioTestCase):
    def source(self, density="spacious"):
        from app.data_loader import PersonalizationSettings
        return render_resume(None, example_draft(), PersonalizationSettings(layout_density=density))

    def compiler(self, page_counts):
        client = CompilerClient("http://unused.invalid")
        client.compile = AsyncMock(side_effect=[
            CompileResponse(success=True, page_count=pages, pdf_base64=f"pdf{pages}") for pages in page_counts
        ])
        return client

    async def test_requested_spacing_is_preserved_if_it_fits(self):
        client = self.compiler([1])
        source = self.source()
        selected, result = await client.compile_resume(source)
        self.assertEqual(selected, source)
        self.assertEqual(result.page_count, 1)
        client.compile.assert_awaited_once_with(source)

    async def test_tighter_spacing_is_tried_before_content_changes(self):
        client = self.compiler([2, 1])
        source = self.source()
        selected, result = await client.compile_resume(source)
        self.assertEqual(selected, source.replace('#let resume-density = "spacious"', '#let resume-density = "comfortable"'))
        self.assertEqual(result.page_count, 1)
        self.assertEqual(client.compile.await_count, 2)
        self.assertIn("spacing pass 2: pages=1", result.compile_log)

    async def test_all_presets_are_bounded_and_preserve_content(self):
        client = self.compiler([3, 2, 2])
        source = self.source()
        selected, result = await client.compile_resume(source)
        self.assertEqual(client.compile.await_count, 3)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(selected, source.replace('#let resume-density = "spacious"', '#let resume-density = "compact"'))

    async def test_compact_never_shrinks_font_or_margins(self):
        client = self.compiler([2])
        source = self.source("compact")
        selected, result = await client.compile_resume(source)
        self.assertEqual(selected, source)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(client.compile.await_count, 1)

    async def test_custom_template_is_compiled_once_without_rewriting(self):
        client = self.compiler([2])
        source = '#set page(height: 200pt)\nCustom layout.'
        selected, _ = await client.compile_resume(source)
        self.assertEqual(selected, source)
        client.compile.assert_awaited_once_with(source)

    async def test_syntax_errors_stop_without_spacing_retries(self):
        client = CompilerClient("http://unused.invalid")
        client.compile = AsyncMock(return_value=CompileResponse(success=False, compile_log="Invalid Typst"))
        source = self.source()
        selected, result = await client.compile_resume(source)
        self.assertEqual(selected, source)
        self.assertFalse(result.success)
        client.compile.assert_awaited_once_with(source)


if __name__ == "__main__":
    unittest.main()
