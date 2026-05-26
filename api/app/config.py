from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    openai_api_key: str | None
    abacus_api_key: str | None
    abacus_base_url: str
    model_name: str
    reasoning_effort: str
    max_compile_retries: int
    max_generator_retries: int
    max_scorer_retries: int
    profile_dir: Path
    output_dir: Path
    compiler_url: str
    public_base_url: str
    enable_demo_mode: bool


def get_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "openai").strip().lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        abacus_api_key=os.getenv("ABACUS_API_KEY") or None,
        abacus_base_url=os.getenv(
            "ABACUS_BASE_URL",
            "https://routellm.abacus.ai/v1",
        ).rstrip("/"),
        model_name=os.getenv("MODEL_NAME", "gpt-5-mini"),
        reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "low"),
        max_compile_retries=int(os.getenv("MAX_COMPILE_RETRIES", "4")),
        max_generator_retries=int(os.getenv("MAX_GENERATOR_RETRIES", "2")),
        max_scorer_retries=int(os.getenv("MAX_SCORER_RETRIES", "2")),
        profile_dir=Path(os.getenv("PROFILE_DIR", "/app/data/profile")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "/app/outputs")),
        compiler_url=os.getenv("COMPILER_URL", "http://typst-compiler:8001").rstrip("/"),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        enable_demo_mode=_as_bool(os.getenv("ENABLE_DEMO_MODE"), default=True),
    )
