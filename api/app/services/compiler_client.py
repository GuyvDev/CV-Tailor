from __future__ import annotations

import base64

import httpx

from app.models import CompileResponse
from app.resume_layout import resume_layout_variants


class CompilerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def compile_resume(self, source: str) -> tuple[str, CompileResponse]:
        """Fit default CVs with spacing alone before requesting content edits."""
        attempts: list[str] = []
        for candidate in resume_layout_variants(source):
            result = await self.compile(candidate)
            attempts.append(f"spacing pass {len(attempts) + 1}: pages={result.page_count}")
            if not result.success or result.page_count == 1:
                break
        if len(attempts) > 1:
            result = result.model_copy(update={
                "compile_log": "\n".join([*attempts, result.compile_log]).strip(),
            })
        return candidate, result

    async def compile(
        self,
        typst_source: str,
        assets: dict[str, bytes] | None = None,
    ) -> CompileResponse:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/compile",
                json={
                    "typst_source": typst_source,
                    "assets_base64": {
                        name: base64.b64encode(content).decode("ascii")
                        for name, content in (assets or {}).items()
                    },
                },
            )
            response.raise_for_status()
        return CompileResponse.model_validate(response.json())
