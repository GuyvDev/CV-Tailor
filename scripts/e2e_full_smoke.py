#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, UTC
from pathlib import Path

API = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "temp" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)


def request(method: str, path: str, payload: dict | None = None, *, timeout: int = 180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def download(path: str, target: Path) -> None:
    with urllib.request.urlopen(API + path, timeout=120) as resp:
        target.write_bytes(resp.read())


def active_profile_id() -> str:
    profiles = request("GET", "/api/profiles")
    for item in profiles:
        if item.get("active"):
            return item["profile_id"]
    return "default"


def wait_job(job_id: str, timeout_s: int = 420) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        last = request("GET", f"/api/jobs/{job_id}")
        if last["status"] in {"completed", "failed", "stopped"}:
            return last
        time.sleep(4)
    raise TimeoutError(f"job {job_id} timed out; last={last}")


def render_png(pdf_path: Path, png_path: Path) -> None:
    subprocess.run(["mutool", "draw", "-o", str(png_path), "-r", "144", str(pdf_path), "1"], check=True)


def visual_check(png_path: Path) -> dict:
    try:
        from PIL import Image
        image = Image.open(png_path).convert("L")
        w, h = image.size
        hist = image.histogram()
        total = w * h
        nonwhite = sum(count for value, count in enumerate(hist) if value < 245)
        dark = sum(count for value, count in enumerate(hist) if value < 120)
        ratio = nonwhite / total if total else 0
        if w < 600 or h < 800 or ratio < 0.01 or dark < 100:
            raise RuntimeError(f"visual check failed: size={w}x{h}, nonwhite_ratio={ratio:.4f}, dark={dark}")
        return {"width": w, "height": h, "nonwhite_ratio": ratio, "dark_pixels": dark}
    except ImportError:
        size = png_path.stat().st_size
        if size < 20_000:
            raise RuntimeError(f"visual check failed: PNG too small ({size} bytes)")
        return {"file_size": size, "note": "PIL unavailable; used size fallback"}


def main() -> int:
    previous = active_profile_id()
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    profile_id = f"e2e-{stamp}"
    summary: dict = {"profile_id": profile_id, "previous_profile_id": previous}
    try:
        request("POST", "/api/profiles", {
            "profile_id": profile_id,
            "display_name": f"E2E Test {stamp}",
            "copy_example": True,
        })
        settings = request("GET", "/api/app-settings")
        provider = settings.get("llm_provider") or "abacus"
        model = settings.get("model_name") or "gpt-5-mini"
        request("POST", "/api/app-settings", {
            "llm_provider": provider,
            "model_name": model,
            "reasoning_effort": settings.get("reasoning_effort") or "low",
            "abacus_base_url": settings.get("abacus_base_url") or "https://routellm.abacus.ai/v1",
            "output_basename": f"e2e-{stamp}-resume",
            "enable_demo_mode": True,
            "openai_api_key": "",
            "abacus_api_key": "",
        })
        summary["settings"] = request("GET", "/api/app-settings")

        source_notes = """
Candidate: Mira Test
Email: mira.test@example.com
Location: Remote
LinkedIn: linkedin.com/in/mira-test
GitHub: github.com/mira-test
Education: B.S. Computer Engineering, Example State University, 2020-2024.
Honors: Robotics Lab Scholar.
Preference: include Robotics Lab Scholar in the profile when relevant.
Avoid repeating GPA in generated resume JSON.
Projects:
1. Edge Telemetry Monitor, 2025, Python, FastAPI, Docker. Built a local service that collected device logs, normalized event payloads, and exposed health summaries through an API.
2. Firmware Diagnostics Toolkit, 2024, C, Python, Linux. Created scripts and test fixtures for serial-device diagnostics and reproducible debug reports.
3. Sensor Dashboard, 2024, TypeScript, React, SQL. Built a dashboard for filtering sensor runs and comparing reliability trends.
Skills: Python, C, TypeScript, FastAPI, React, Docker, Linux, SQL, testing, debugging.
Writing preferences: concise, factual, no invented metrics.
""".strip()
        draft = request("POST", "/api/profile/ai-draft", {"source_text": source_notes}, timeout=240)
        summary["ai_draft_summary"] = draft["summary"]
        profile = draft["profile"]
        request("POST", "/api/profile", {
            "master_profile": profile["master_profile"],
            "projects_json": profile["projects_json"],
            "skills_json": profile["skills_json"],
            "rules": profile["rules"],
            "research_guidelines": profile["research_guidelines"],
            "personalization": profile["personalization"],
            "initialize_templates": True,
        })
        saved = request("GET", "/api/profile")
        projects = json.loads(saved["projects_json"])
        skills = json.loads(saved["skills_json"])
        if len(saved["master_profile"]) < 200 or not projects or not any(skills.values()):
            raise RuntimeError("AI draft/save validation failed: profile content is too sparse")
        summary["saved_profile"] = {
            "profile_id": saved["profile_id"],
            "master_profile_len": len(saved["master_profile"]),
            "project_count": len(projects),
            "skill_bucket_count": len(skills),
            "personalization": saved["personalization"],
        }

        role_description = """
Embedded Systems Software Engineer
We are hiring an engineer to build Linux-based diagnostics tools, device telemetry services, and dashboards for hardware validation teams. The role uses Python, C, FastAPI, Docker, Linux, serial-device debugging, automated tests, and clear technical documentation.
""".strip()
        generated = request("POST", "/api/generate", {
            "job_description": role_description,
            "role_focus": "systems",
            "template": "default",
            "label": "e2e-embedded-systems",
        })
        job = wait_job(generated["job_id"])
        summary["job"] = job
        if job["status"] != "completed":
            raise RuntimeError(f"generation failed: {job}")
        if (job.get("quality_score") or 0) < 40 or (job.get("match_score") or 0) < 30:
            raise RuntimeError(f"generated resume scored too low for seeded profile: {job}")
        pdf_path = OUT / f"{profile_id}.pdf"
        png_path = OUT / f"{profile_id}.png"
        download(job["pdf_url"], pdf_path)
        render_png(pdf_path, png_path)
        summary["pdf_path"] = str(pdf_path)
        summary["png_path"] = str(png_path)
        summary["visual_check"] = visual_check(png_path)
        (OUT / f"{profile_id}-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        try:
            request("POST", "/api/profiles/active", {"profile_id": previous})
        except Exception as exc:
            print(f"WARNING: failed to restore active profile {previous}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
