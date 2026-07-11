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
    app_env: str
    api_auth_token: str | None
    require_api_auth: bool
    allowed_origins: tuple[str, ...]
    enable_docs: bool
    public_files_enabled: bool


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())


def _production_mode() -> bool:
    return _as_bool(os.getenv("PRODUCTION"), default=False) or os.getenv("APP_ENV", "").strip().lower() == "production"


def get_settings() -> Settings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    production = _production_mode()
    allowed_origins = os.getenv("API_ALLOWED_ORIGINS")
    if allowed_origins is None:
        allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS")
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
        app_env=app_env,
        api_auth_token=os.getenv("API_AUTH_TOKEN") or None,
        require_api_auth=production or _as_bool(os.getenv("REQUIRE_API_AUTH"), default=False),
        allowed_origins=_csv(
            allowed_origins
            if allowed_origins is not None
            else "" if production else "http://localhost:3000,http://127.0.0.1:3000"
        ),
        enable_docs=_as_bool(os.getenv("API_ENABLE_DOCS"), default=not production),
        public_files_enabled=_as_bool(os.getenv("API_PUBLIC_FILES_ENABLED"), default=not production),
    )
