from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from pypdf import PdfReader


class CompileRequest(BaseModel):
    typst_source: str


class CompileResponse(BaseModel):
    success: bool
    page_count: int | None = None
    compile_log: str = ""
    pdf_base64: str | None = None


TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/typst"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="typst-compiler", version="0.1.0")


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/compile", response_model=CompileResponse)
async def compile_typst(payload: CompileRequest) -> CompileResponse:
    with tempfile.TemporaryDirectory(dir=TMP_DIR) as tmp_dir:
        source_path = Path(tmp_dir) / "resume.typ"
        pdf_path = Path(tmp_dir) / "resume.pdf"
        source_path.write_text(payload.typst_source, encoding="utf-8")

        command = [
            "typst",
            "compile",
            str(source_path),
            str(pdf_path),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        compile_log = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        if result.returncode != 0 or not pdf_path.exists():
            return CompileResponse(success=False, compile_log=compile_log)

        page_count = len(PdfReader(str(pdf_path)).pages)
        pdf_bytes = pdf_path.read_bytes()
        return CompileResponse(
            success=True,
            page_count=page_count,
            compile_log=compile_log,
            pdf_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
        )
