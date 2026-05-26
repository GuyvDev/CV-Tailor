from __future__ import annotations

import httpx

from app.models import CompileResponse


class CompilerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def compile(self, typst_source: str) -> CompileResponse:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/compile",
                json={"typst_source": typst_source},
            )
            response.raise_for_status()
        return CompileResponse.model_validate(response.json())
