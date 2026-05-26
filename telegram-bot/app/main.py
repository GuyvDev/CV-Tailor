from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_IDS: set[int] = set(
    int(x.strip())
    for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if x.strip()
)
API_URL = os.environ.get("API_URL", "http://api:8000")

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

ROLE_OPTIONS = {"ai", "systems", "software", "architecture", "general"}
URL_RE = re.compile(r"https?://[^\s<>\"'{}|\\^`\[\]]+")
TEXT_UPLOAD_EXTENSIONS = {".txt", ".md"}
MAX_TEXT_UPLOAD_BYTES = 128 * 1024
TEXT_GENERATION_TAG = "#cv"
OUTPUT_BASENAME = os.environ.get("OUTPUT_BASENAME", "tailored-resume")
OUTPUT_PDF_FILENAME = f"{OUTPUT_BASENAME}.pdf"
OUTPUT_TYPST_FILENAME = f"{OUTPUT_BASENAME}.typ"
AUTO_ONE_PAGE_EDIT_INSTRUCTIONS = (
    "Make this fit on exactly one page. Keep the same role target and verified facts. "
    "Shorten the profile to at most 2 tight sentences, compress wording, trim weaker bullets, "
    "and remove low-value redundancy without adding new facts."
)
INVALID_SCRAPE_MARKERS = (
    "{{",
    "}}",
    "an error occured. please try again",
    "an error occurred. please try again",
    "currently no open positions",
    "we don't have any open positions",
    "no positions match your filter",
    "position.name",
    "company.name",
    "field.name",
    "getlocationname(",
)
COMEET_HOSTS = {"comeet.com", "www.comeet.com", "comeet.co", "www.comeet.co"}

STAGE_LABELS: dict[str, str] = {
    "queued": "Queued...",
    "loading_profile": "Loading profile...",
    "gen_attempt_1": "Generating CV (gen 1/2)...",
    "gen_attempt_2": "Revising CV (gen 2/2)...",
    "compile_attempt_1": "Compiling PDF (attempt 1/2)...",
    "compile_attempt_2": "Compiling PDF (attempt 2/2)...",
    "scr_attempt_1": "Scoring CV (scr 1/2)...",
    "scr_attempt_2": "Scoring CV (scr 2/2)...",
    "compiling_approved_draft": "Compiling approved edit...",
    "compressing_approved_draft": "Compressing approved edit to one page...",
    "compiling_approved_draft_retry": "Compiling compressed approved edit...",
    "completed": "Done!",
    "failed": "Failed",
    "stopped": "Stopped.",
}

ACTIVE_JOB_STATUSES = {"queued", "running"}
EDITABLE_JOB_STATUSES = {"completed"}
REMOVABLE_JOB_STATUSES = {"completed", "failed", "stopped"}


def stage_label(stage: str | None) -> str:
    return STAGE_LABELS.get(stage or "", "Starting...")


# ── helpers ──────────────────────────────────────────────────────────────────

def is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return update.effective_user.id in ALLOWED_USER_IDS


def parse_message(text: str) -> list[tuple[str, str | None]]:
    results: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        urls_in_line = URL_RE.findall(line)
        if not urls_in_line:
            continue
        role: str | None = None
        prefix = line.split(":", 1)[0].strip().lower()
        if prefix in ROLE_OPTIONS and prefix != "general":
            role = prefix
        for url in urls_in_line:
            results.append((url, role))
    return results


def parse_role_focus_hint(text: str) -> str | None:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    first_token = normalized.split()[0].rstrip(":")
    if first_token in ROLE_OPTIONS and first_token != "general":
        return first_token
    role_match = re.search(r"\brole\s*:\s*([a-z]+)\b", normalized)
    if role_match:
        role = role_match.group(1)
        if role in ROLE_OPTIONS and role != "general":
            return role
    return None


def parse_caption_payload(text: str) -> tuple[str | None, str]:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    role_focus: str | None = None
    extra_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        parsed_role = parse_role_focus_hint(line)
        normalized = line.lower()
        if parsed_role is not None and (
            normalized == parsed_role
            or normalized == f"{parsed_role}:"
            or normalized == f"role: {parsed_role}"
        ):
            if role_focus is None:
                role_focus = parsed_role
            continue
        extra_lines.append(raw_line.strip())

    return role_focus, _clean("\n".join(extra_lines))


def parse_tagged_text_request(text: str) -> tuple[str | None, str] | None:
    lines = [line.rstrip() for line in text.splitlines()]
    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty:
        return None

    first = nonempty[0]
    lowered_first = first.lower()
    if not lowered_first.startswith(TEXT_GENERATION_TAG):
        return None

    role_focus: str | None = None
    suffix = first[len(TEXT_GENERATION_TAG):].strip()
    if suffix:
        suffix_token = suffix.rstrip(":").strip().lower()
        if suffix_token in ROLE_OPTIONS and suffix_token != "general":
            role_focus = suffix_token

    body_lines = nonempty[1:]
    if body_lines:
        first_body = body_lines[0].strip()
        parsed_role = parse_role_focus_hint(first_body)
        if parsed_role is not None and (
            first_body.lower() == parsed_role
            or first_body.lower() == f"{parsed_role}:"
            or first_body.lower() == f"role: {parsed_role}"
        ):
            role_focus = parsed_role
            body_lines = body_lines[1:]

    body = _clean("\n".join(body_lines))
    return role_focus, body


def sanitize_label(value: str) -> str:
    stem = Path(value).stem.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-_.")
    return (cleaned or "upload")[:40]


def decode_text_upload(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1255", "latin-1"):
        try:
            text = raw.decode(encoding)
            if text.strip():
                return text
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode the uploaded text file.")


async def read_uploaded_text(message: Message) -> tuple[str, str]:
    document = message.document
    if document is None:
        raise ValueError("No document was attached.")
    filename = document.file_name or "upload.txt"
    ext = Path(filename).suffix.lower()
    mime_type = (document.mime_type or "").lower()
    if ext not in TEXT_UPLOAD_EXTENSIONS and not mime_type.startswith("text/"):
        raise ValueError("Please upload a `.txt` or `.md` text file.")
    if document.file_size and document.file_size > MAX_TEXT_UPLOAD_BYTES:
        raise ValueError("The text file is too large. Keep it under 128 KB.")

    telegram_file = await document.get_file()
    raw = await telegram_file.download_as_bytearray()
    text = _clean(decode_text_upload(bytes(raw)))
    if len(text) < 20:
        raise ValueError("The uploaded text is too short for CV generation.")
    return filename, text


async def scrape_job_description(url: str) -> str:
    async with httpx.AsyncClient(
        headers=SCRAPE_HEADERS, follow_redirects=True, timeout=30
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    final_url = str(resp.url)
    if _is_comeet_url(final_url):
        comeet_text = _extract_comeet_job_description(resp.text, final_url)
        if _is_usable_job_description(comeet_text):
            return comeet_text

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    candidates = [
        ".job-description", "#job-description", ".description__text",
        "[data-testid='job-description']", ".jobsearch-jobDescriptionText",
        ".job-details", ".job-content", ".posting-requirements",
        ".jobs-description", "#jobDescriptionText", "article", "main",
    ]
    for selector in candidates:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(separator="\n", strip=True)
            if len(text) > 300 and _is_usable_job_description(text):
                return _clean(text)

    # Some job boards, including certain Workday pages, keep the human-visible
    # description client-side but still expose a full SEO/share description.
    for selector in (
        "meta[property='og:description']",
        "meta[name='description']",
        "meta[name='twitter:description']",
    ):
        node = soup.select_one(selector)
        if node:
            content = _clean(node.get("content", ""))
            if len(content) > 80 and _is_usable_job_description(content):
                return content

    fallback = _clean(soup.get_text(separator="\n", strip=True))
    if not _is_usable_job_description(fallback):
        raise ValueError(
            "Could not extract a usable job description from that page. "
            "The scraped content looked like an error/template page, not a live posting. "
            "Paste the description as `#cv ...` text or upload it as a `.txt` / `.md` file."
        )
    return fallback


def _is_comeet_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in COMEET_HOSTS


def _json_variable(html_text: str, variable_name: str) -> object | None:
    match = re.search(rf"\b{re.escape(variable_name)}\s*=\s*", html_text)
    if not match:
        return None
    raw = html_text[match.end():].lstrip()
    try:
        value, _end = json.JSONDecoder().raw_decode(raw)
        return value
    except json.JSONDecodeError:
        logger.debug("Could not parse Comeet %s JSON data", variable_name)
        return None


def _html_fragment_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return BeautifulSoup(value, "lxml").get_text(separator="\n", strip=True)


def _comeet_position_to_text(position: dict) -> str:
    custom_fields = position.get("custom_fields")
    if not isinstance(custom_fields, dict):
        custom_fields = {}
    details = custom_fields.get("details", [])
    if isinstance(details, list):
        details = sorted(
            (item for item in details if isinstance(item, dict)),
            key=lambda item: item.get("order") or 0,
        )
    else:
        details = []

    location = position.get("location") if isinstance(position.get("location"), dict) else {}
    header_values = [
        ("Job title", position.get("name")),
        ("Company", position.get("company_name")),
        ("Department", position.get("department")),
        ("Location", location.get("displayName") or location.get("name")),
        ("Employment type", position.get("employment_type")),
        ("Experience level", position.get("experience_level")),
        ("Workplace type", position.get("workplace_type")),
    ]
    lines = [
        f"{label}: {str(value).strip()}"
        for label, value in header_values
        if value is not None and str(value).strip()
    ]

    for item in details:
        name = str(item.get("name") or "Details").strip()
        text = _html_fragment_text(item.get("value"))
        if text:
            lines.extend(["", name, text])

    return _clean("\n".join(lines))


def _comeet_target_uid(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 5 and parts[0] == "jobs":
        return parts[-1]
    return None


def _extract_comeet_job_description(html_text: str, url: str) -> str:
    position_data = _json_variable(html_text, "POSITION_DATA")
    if isinstance(position_data, dict):
        text = _comeet_position_to_text(position_data)
        if text:
            return text

    target_uid = _comeet_target_uid(url)
    positions_data = _json_variable(html_text, "COMPANY_POSITIONS_DATA")
    if target_uid and isinstance(positions_data, list):
        for position in positions_data:
            if not isinstance(position, dict):
                continue
            urls = [
                str(position.get("url_comeet_hosted_page") or ""),
                str(position.get("url_recruit_hosted_page") or ""),
                str(position.get("url_active_page") or ""),
            ]
            if position.get("uid") == target_uid or any(target_uid in value for value in urls):
                text = _comeet_position_to_text(position)
                if text:
                    return text

    return ""


def _is_usable_job_description(text: str) -> bool:
    cleaned = _clean(text)
    if len(cleaned) < 80:
        return False
    normalized = cleaned.lower()
    return not any(marker in normalized for marker in INVALID_SCRAPE_MARKERS)


def _clean(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\xa0", " ")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n".join(lines)[:8000]


def _response_detail_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        text = response.text.strip()
        return text[:300] if text else f"HTTP {response.status_code}"

    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail[:3]:
            if not isinstance(item, dict):
                continue
            loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
            msg = str(item.get("msg", "")).strip()
            if loc and msg:
                parts.append(f"{loc}: {msg}")
            elif msg:
                parts.append(msg)
        if parts:
            return "; ".join(parts)
    return str(payload)[:300]


def _format_network_error(target: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = _response_detail_text(exc.response)
        if target == "api":
            return f"API request failed with HTTP {status}: {detail}"
        return f"HTTP {status} while reaching {target}: {detail}"
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if (
        "temporary failure in name resolution" in lowered
        or "name or service not known" in lowered
        or "nodename nor servname provided" in lowered
    ):
        if target == "api":
            return (
                "Could not resolve the `api` service from the bot container. "
                "The `api` container is probably stopped, or Docker DNS inside the VM is unhealthy."
            )
        return f"DNS lookup failed while reaching {target}: {message}"
    if "connection refused" in lowered or "all connection attempts failed" in lowered:
        if target == "api":
            return (
                f"Could not connect to the API at `{API_URL}`. "
                "The `api` container is probably down or still starting."
            )
        return f"Could not connect to {target}: {message}"
    return f"Network error while reaching {target}: {message}"


async def submit_job(
    job_description: str,
    role_focus: str | None,
    label: str = "",
    source_url: str = "",
    source_job_id: str = "",
    edit_instructions: str | None = None,
    approved_draft: dict | None = None,
) -> str:
    logger.info(f"Submitting job to API with role_focus={role_focus}, label={label!r}")
    payload: dict = {
        "job_description": job_description,
        "role_focus": role_focus,
        "template": "default",
        "label": label,
        "source_url": source_url,
        "source_job_id": source_job_id,
    }
    if edit_instructions:
        payload["edit_instructions"] = edit_instructions
    if approved_draft is not None:
        payload["approved_draft"] = approved_draft
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{API_URL}/api/generate", json=payload)
            resp.raise_for_status()
            job_id = resp.json()["job_id"]
            logger.info(f"Job submitted successfully: {job_id}")
            return job_id
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc


async def submit_edit_preview(source_job_id: str, edit_instructions: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{API_URL}/api/edit-preview",
                json={
                    "source_job_id": source_job_id,
                    "edit_instructions": edit_instructions,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc


async def request_score_resume(source_job_id: str, job_description: str | None = None) -> dict:
    payload: dict = {"source_job_id": source_job_id}
    if job_description is not None:
        payload["job_description"] = job_description
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{API_URL}/api/score-existing", json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc


async def _safe_edit(msg: Message, text: str, parse_mode: str = "Markdown", reply_markup=None) -> None:
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Failed to edit status message: {e}")


async def poll_job(job_id: str, status_msg: Message | None, domain: str, max_seconds: int = 300) -> dict:
    logger.info(f"Polling job {job_id}...")
    last_stage = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for _ in range(max_seconds // 5):
                await asyncio.sleep(5)
                resp = await client.get(f"{API_URL}/api/jobs/{job_id}")
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                stage = data.get("stage")
                logger.info(f"Job {job_id} status: {status}, stage: {stage}")
                if stage != last_stage:
                    last_stage = stage
                    if status_msg is not None:
                        await _safe_edit(status_msg, f"*{domain}*\n{stage_label(stage)}")
                if status in ("completed", "failed", "stopped"):
                    logger.info(f"Job {job_id} finished with status: {status}")
                    if status == "failed" and status_msg is not None:
                        error = data.get("error", "Unknown error")
                        compile_logs = data.get("compile_logs", [])
                        text = f"*{domain}*\nFailed: {error}"
                        if compile_logs:
                            text += f"\n\nCompile log:\n```\n{compile_logs[-1][:400]}\n```"
                        await _safe_edit(status_msg, text)
                    elif status == "stopped" and status_msg is not None:
                        error = data.get("error", "Stopped.")
                        await _safe_edit(status_msg, f"*{domain}*\n{error}")
                    return data
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc
    logger.warning(f"Job {job_id} timed out after {max_seconds}s")
    if status_msg is not None:
        await _safe_edit(status_msg, f"*{domain}*\nTimed out after {max_seconds}s")
    return {"status": "timeout", "error": "Timed out waiting for result."}


async def download_artifact(url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(f"{API_URL}{url}")
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc


async def download_pdf(pdf_url: str) -> bytes:
    return await download_artifact(pdf_url)


async def download_typst(typst_url: str) -> bytes:
    return await download_artifact(typst_url)


async def fetch_cv_list() -> list[dict]:
    """Return all jobs from the API sorted newest first."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{API_URL}/api/jobs")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc


def filter_jobs_for_action(jobs: list[dict], action: str) -> list[dict]:
    if action == "edit":
        return [job for job in jobs if job.get("status") in EDITABLE_JOB_STATUSES]
    if action == "remove":
        return [job for job in jobs if job.get("status") in REMOVABLE_JOB_STATUSES]
    if action == "stop":
        return [job for job in jobs if job.get("status") in ACTIVE_JOB_STATUSES]
    return jobs


def _job_icon(status: str) -> str:
    if status == "completed":
        return "✓"
    if status == "stopped":
        return "■"
    if status == "failed":
        return "✗"
    return "…"


def _job_summary_line(job: dict) -> str:
    short_id = job["job_id"][:8]
    label = job.get("label") or "?"
    title = job.get("job_title") or ""
    status = job.get("status", "")
    prefix = f"{_job_icon(status)} {short_id} | {label}"
    return prefix + (f" | {title}" if title else "")


def _cv_button_label(job: dict) -> str:
    return _job_summary_line(job)[:60]


def build_cv_keyboard(action: str, jobs: list[dict]) -> InlineKeyboardMarkup:
    """One button per CV, one cancel button at the bottom."""
    rows = [
        [InlineKeyboardButton(_cv_button_label(j), callback_data=f"{action}:{j['job_id']}")]
        for j in jobs
    ]
    rows.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def _normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _picker_text(action: str, jobs: list[dict]) -> str:
    if not jobs:
        return f"No CVs available to {action}."
    lines = [f"Select a CV to {action}:"]
    for job in jobs[:12]:
        lines.append(_job_summary_line(job))
    if len(jobs) > 12:
        lines.append(f"...and {len(jobs) - 12} more")
    return "\n".join(lines)


def _picker_sessions(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict]:
    return context.user_data.setdefault("picker_sessions", {})


def _create_picker_session(
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    jobs: list[dict],
) -> tuple[str, dict]:
    sessions = _picker_sessions(context)
    session_id = secrets.token_hex(3)
    session = {
        "action": action,
        "jobs": jobs,
        "selected": [],
    }
    sessions[session_id] = session
    return session_id, session


def _get_picker_session(context: ContextTypes.DEFAULT_TYPE, session_id: str) -> dict | None:
    return _picker_sessions(context).get(session_id)


def _drop_picker_session(context: ContextTypes.DEFAULT_TYPE, session_id: str) -> None:
    _picker_sessions(context).pop(session_id, None)


def _set_picker_selection(session: dict, job_ids: list[str]) -> None:
    allowed_ids = {job["job_id"] for job in session["jobs"]}
    session["selected"] = [job_id for job_id in job_ids if job_id in allowed_ids]


def _toggle_picker_selection(session: dict, job_id: str) -> None:
    selected = set(session["selected"])
    if job_id in selected:
        selected.remove(job_id)
    else:
        selected.add(job_id)
    _set_picker_selection(session, list(selected))


def _selected_jobs(session: dict) -> list[dict]:
    selected_ids = set(session["selected"])
    return [job for job in session["jobs"] if job["job_id"] in selected_ids]


def _multi_picker_text(session: dict) -> str:
    action = session["action"]
    jobs = session["jobs"]
    selected = set(session["selected"])
    verb = "remove" if action == "remove" else "stop"
    lines = [f"Select CVs to {verb}:", f"Selected: {len(selected)} / {len(jobs)}"]
    for job in jobs[:12]:
        mark = "☑" if job["job_id"] in selected else "☐"
        lines.append(f"{mark} {_job_summary_line(job)}")
    if len(jobs) > 12:
        lines.append(f"...and {len(jobs) - 12} more")
    if action == "remove":
        lines.append("Use Remove selected or Remove all.")
    else:
        lines.append("Use Stop selected.")
    return "\n".join(lines)


def build_multi_cv_keyboard(session_id: str, session: dict) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                ("☑ " if job["job_id"] in set(session["selected"]) else "☐ ") + job["job_id"][:8],
                callback_data=f"picker_toggle:{session_id}:{job['job_id']}",
            )
        ]
        for job in session["jobs"]
    ]
    rows.append([
        InlineKeyboardButton("Select all", callback_data=f"picker_select_all:{session_id}"),
        InlineKeyboardButton("Clear", callback_data=f"picker_clear:{session_id}"),
    ])
    if session["action"] == "remove":
        rows.append([
            InlineKeyboardButton("Remove selected", callback_data=f"picker_remove_selected:{session_id}"),
            InlineKeyboardButton("Remove all", callback_data=f"picker_remove_all:{session_id}"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("Stop selected", callback_data=f"picker_stop_selected:{session_id}"),
        ])
    rows.append([InlineKeyboardButton("Cancel", callback_data=f"picker_cancel:{session_id}")])
    return InlineKeyboardMarkup(rows)


def _confirm_picker_text(action: str, jobs: list[dict], *, mode: str) -> str:
    label = "all listed CVs" if mode == "all" else "the selected CVs"
    lines = [f"Confirm {action} for {label}:"]
    for job in jobs[:15]:
        lines.append(_job_summary_line(job))
    if len(jobs) > 15:
        lines.append(f"...and {len(jobs) - 15} more")
    return "\n".join(lines)


def build_confirm_keyboard(session_id: str, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes", callback_data=f"picker_confirm_remove:{session_id}:{mode}"),
            InlineKeyboardButton("Back", callback_data=f"picker_back:{session_id}"),
        ],
        [InlineKeyboardButton("Cancel", callback_data=f"picker_cancel:{session_id}")],
    ])


async def fetch_job(job_id: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{API_URL}/api/jobs/{job_id}")
            if resp.status_code == 200:
                return resp.json()
        return None
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error("api", exc)) from exc


async def request_stop_job(job_id: str) -> tuple[dict | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{API_URL}/api/jobs/{job_id}/stop")
            if resp.status_code == 200:
                return resp.json(), None
            if resp.status_code == 404:
                return None, "Job not found."
            return None, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return None, _format_network_error("api", exc)


async def request_delete_job(job_id: str) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(f"{API_URL}/api/jobs/{job_id}")
            if resp.status_code == 204:
                return True, None
            if resp.status_code == 404:
                return False, "Job not found."
            if resp.status_code == 409:
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                return False, detail or "Stop the job before deleting it."
            return False, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, _format_network_error("api", exc)


async def resolve_original(
    query: str,
    *,
    allowed_statuses: set[str] | None = None,
) -> tuple[dict | None, str | None]:
    jobs = await fetch_cv_list()
    if allowed_statuses is not None:
        jobs = [job for job in jobs if job.get("status") in allowed_statuses]
    query = query.strip().lower()
    if not query:
        return None, "Please provide a CV ID."

    exact_id_matches = [job for job in jobs if job["job_id"].lower() == query]
    if len(exact_id_matches) == 1:
        return await fetch_job(exact_id_matches[0]["job_id"]), None

    prefix_matches = [job for job in jobs if job["job_id"].lower().startswith(query)]
    if len(prefix_matches) == 1:
        return await fetch_job(prefix_matches[0]["job_id"]), None
    if len(prefix_matches) > 1:
        return None, "More than one CV matches that ID prefix. Use more characters or pick from the list."

    normalized_query = _normalize_lookup(query)
    if normalized_query:
        label_matches = [
            job for job in jobs
            if _normalize_lookup(job.get("label", "")) == normalized_query
        ]
        if len(label_matches) == 1:
            return await fetch_job(label_matches[0]["job_id"]), None
        if len(label_matches) > 1:
            return None, "More than one CV has that label. Use the ID from the list instead."

    return None, "No CV found with that ID."


def _empty_picker_message(action: str) -> str:
    if action == "edit":
        return "No completed CVs found."
    if action == "remove":
        return "No saved CVs found to remove."
    if action == "stop":
        return "No active jobs to stop."
    return "No CVs found."


async def send_cv_picker(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
) -> None:
    jobs = filter_jobs_for_action(await fetch_cv_list(), action)
    if not jobs:
        await message.reply_text(_empty_picker_message(action))
        return
    if action == "edit":
        keyboard = build_cv_keyboard("edit_select", jobs)
        await message.reply_text(_picker_text(action, jobs), reply_markup=keyboard)
        return
    session_id, session = _create_picker_session(context, action, jobs)
    await message.reply_text(
        _multi_picker_text(session),
        reply_markup=build_multi_cv_keyboard(session_id, session),
    )


def _format_draft_for_diff(draft: dict) -> list[str]:
    lines: list[str] = []
    lines.append(f"Headline: {draft.get('headline', '')}")
    lines.append(f"Profile: {draft.get('profile', '')}")

    education = draft.get("education", {})
    lines.append("Education:")
    for bullet in education.get("bullets", []):
        lines.append(f"  - {bullet}")

    lines.append("Projects:")
    for project in draft.get("projects", []):
        stack = " / ".join(project.get("stack", []))
        header = project.get("title", "")
        if stack:
            header += f" | {stack}"
        if project.get("dates"):
            header += f" | {project['dates']}"
        lines.append(f"  * {header}")
        for bullet in project.get("bullets", []):
            lines.append(f"    - {bullet}")

    lines.append("Skills:")
    for bucket in draft.get("skills", []):
        items = ", ".join(bucket.get("items", []))
        lines.append(f"  * {bucket.get('category', '')}: {items}")

    lines.append(f"Fit summary: {draft.get('fit_summary', '')}")
    keywords = ", ".join(draft.get("keywords", []))
    lines.append(f"Keywords: {keywords}")
    return lines


def _edit_diff_text(before_draft: dict, after_draft: dict) -> str:
    diff_lines = list(
        difflib.unified_diff(
            _format_draft_for_diff(before_draft),
            _format_draft_for_diff(after_draft),
            fromfile="before",
            tofile="after",
            lineterm="",
            n=1,
        )
    )
    if not diff_lines:
        return "No visible changes were detected."
    text = "\n".join(diff_lines)
    if len(text) > 3200:
        text = text[:3200].rstrip() + "\n...diff truncated..."
    return text


def _score_findings_text(metadata: dict) -> str | None:
    quality = metadata.get("quality_score")
    match = metadata.get("match_score")
    band = metadata.get("score_band")
    review_summary = (metadata.get("review_summary") or "").strip()
    recommendations = [
        str(item).strip()
        for item in (metadata.get("recommendations") or [])
        if str(item).strip()
    ]

    if quality is None and match is None and not band and not review_summary and not recommendations:
        return None

    lines: list[str] = ["Scorer results"]
    score_bits: list[str] = []
    if quality is not None:
        score_bits.append(f"Quality: {quality}/100")
    if match is not None:
        score_bits.append(f"Match: {match}/100")
    if band:
        score_bits.append(f"Band: {band}")
    if score_bits:
        lines.append(" | ".join(score_bits))
    if review_summary:
        lines.append(f"Findings: {review_summary}")
    if recommendations:
        lines.append("Top recommendations:")
        for item in recommendations[:2]:
            lines.append(f"- {item}")
    text = "\n".join(lines)
    return text[:3500]


def _score_report_message(report: dict, *, label: str, against_original: bool) -> str:
    quality = report.get("quality_score")
    match = report.get("match_score")
    band = report.get("score_band", "")
    decision = report.get("decision", "")
    summary = (report.get("summary") or "").strip()
    gaps = [str(item).strip() for item in (report.get("gaps") or []) if str(item).strip()]
    recommendations = [
        str(item).strip()
        for item in (report.get("recommendations") or [])
        if str(item).strip()
    ]

    lines = [
        f"Scored CV `{label}`",
        "Against: original job description" if against_original else "Against: new job description",
    ]
    score_bits: list[str] = []
    if quality is not None:
        score_bits.append(f"Quality: {quality}/100")
    if match is not None:
        score_bits.append(f"Match: {match}/100")
    if band:
        score_bits.append(f"Band: {band}")
    if decision:
        score_bits.append(f"Decision: {decision}")
    if score_bits:
        lines.append(" | ".join(score_bits))
    if summary:
        lines.append(f"Summary: {summary}")
    if gaps:
        lines.append("Top gaps:")
        for item in gaps[:2]:
            lines.append(f"- {item}")
    if recommendations:
        lines.append("Top recommendations:")
        for item in recommendations[:2]:
            lines.append(f"- {item}")
    return "\n".join(lines)[:3500]


def _preview_sessions(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict]:
    return context.user_data.setdefault("edit_preview_sessions", {})


def _create_preview_session(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    original: dict,
    instructions: str,
    preview: dict,
) -> str:
    session_id = secrets.token_hex(4)
    _preview_sessions(context)[session_id] = {
        "original": original,
        "instructions": instructions,
        "preview": preview,
    }
    return session_id


def _get_preview_session(context: ContextTypes.DEFAULT_TYPE, session_id: str) -> dict | None:
    return _preview_sessions(context).get(session_id)


def _drop_preview_session(context: ContextTypes.DEFAULT_TYPE, session_id: str) -> None:
    _preview_sessions(context).pop(session_id, None)


def build_edit_preview_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"edit_preview_approve:{session_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"edit_preview_cancel:{session_id}"),
        ]
    ])


def _retry_sessions(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict]:
    return context.user_data.setdefault("edit_retry_sessions", {})


def _create_retry_session(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    original: dict,
    instructions: str,
) -> str:
    session_id = secrets.token_hex(4)
    _retry_sessions(context)[session_id] = {
        "original": original,
        "instructions": instructions,
    }
    return session_id


def _get_retry_session(context: ContextTypes.DEFAULT_TYPE, session_id: str) -> dict | None:
    return _retry_sessions(context).get(session_id)


def _drop_retry_session(context: ContextTypes.DEFAULT_TYPE, session_id: str) -> None:
    _retry_sessions(context).pop(session_id, None)


def build_one_page_retry_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Shrink to one page", callback_data=f"edit_retry_one_page:{session_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"edit_retry_cancel:{session_id}"),
        ]
    ])


async def run_approved_edit(
    context: ContextTypes.DEFAULT_TYPE,
    reply_message: Message,
    original: dict,
    instructions: str,
    approved_draft: dict,
    status_msg: Message,
) -> None:
    label = original.get("extra", {}).get("label") or original.get("job_id", "")[:8]
    job_description = original.get("extra", {}).get("job_description", "")
    if not job_description:
        await _safe_edit(status_msg, f"*{label}*\nOriginal description not stored — cannot edit.")
        return

    role_focus = original.get("role_focus")
    source_job_id = original.get("job_id", "")
    source_url = original.get("extra", {}).get("source_url", "")

    await _safe_edit(status_msg, f"*{label}*\nSubmitting approved edit...")

    try:
        job_id = await submit_job(
            job_description,
            role_focus,
            label=label,
            source_url=source_url,
            source_job_id=source_job_id,
            edit_instructions=instructions,
            approved_draft=approved_draft,
        )
    except Exception as e:
        await _safe_edit(status_msg, f"*{label}*\nError submitting approved edit: `{e}`")
        return

    metadata = await poll_job(job_id, status_msg, label)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if metadata.get("status") == "completed" and metadata.get("pdf_url"):
        pdf_bytes = await download_pdf(metadata["pdf_url"])
        typ_bytes = (
            await download_typst(metadata["typst_url"])
            if metadata.get("typst_url")
            else None
        )
        short_id = metadata.get("job_id", "")[:8]
        job_title = metadata.get("extra", {}).get("job_title", "")
        line1 = f"`{short_id}` — {job_title} _(edited)_" if job_title else f"`{short_id}` — {label} _(edited)_"
        caption_parts = [line1]
        if metadata.get("fit_summary"):
            caption_parts.append(metadata["fit_summary"])
        caption = "\n\n".join(caption_parts)[:1024]
        await reply_message.reply_document(
            document=pdf_bytes,
            filename=OUTPUT_PDF_FILENAME,
            caption=caption,
            parse_mode="Markdown",
        )
        if typ_bytes is not None:
            await reply_message.reply_document(
                document=typ_bytes,
                filename=OUTPUT_TYPST_FILENAME,
            )
        score_text = _score_findings_text(metadata)
        if score_text:
            await reply_message.reply_text(score_text)
    elif metadata.get("status") == "stopped":
        await reply_message.reply_text(
            f"[edited] {label}\nStopped.",
            parse_mode="Markdown",
        )
    else:
        error = metadata.get("error", "Unknown error")
        compile_logs = metadata.get("compile_logs", [])
        error_text = f"[edited] {label}\nFailed: {error}"
        if compile_logs:
            error_text += f"\n\nCompile log:\n```\n{compile_logs[-1][:400]}\n```"
        await reply_message.reply_text(error_text, parse_mode="Markdown")
        if "did not fit on one page" in error.lower():
            retry_session_id = _create_retry_session(
                context,
                original=original,
                instructions=instructions,
            )
            await reply_message.reply_text(
                "Use the button below to generate a compressed one-page edit preview from the same CV.",
                reply_markup=build_one_page_retry_keyboard(retry_session_id),
            )


async def run_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    original: dict,
    instructions: str,
    status_msg: Message,
) -> None:
    label = original.get("extra", {}).get("label") or original.get("job_id", "")[:8]
    job_description = original.get("extra", {}).get("job_description", "")
    if not job_description:
        await _safe_edit(status_msg, f"*{label}*\nOriginal description not stored — cannot edit.")
        return

    source_job_id = original.get("job_id", "")

    await _safe_edit(status_msg, f"*{label}*\nPreparing edit preview...")

    try:
        preview = await submit_edit_preview(source_job_id, instructions)
    except Exception as e:
        await _safe_edit(status_msg, f"*{label}*\nError generating preview: `{e}`")
        return

    try:
        await status_msg.delete()
    except Exception:
        pass

    session_id = _create_preview_session(
        context,
        original=original,
        instructions=instructions,
        preview=preview,
    )
    diff_text = _edit_diff_text(preview["before_draft"], preview["after_draft"])
    title = preview["after_draft"].get("job_title") or original.get("extra", {}).get("job_title", "")
    text = (
        f"Edit preview for {source_job_id[:8]}"
        + (f" — {title}" if title else "")
        + "\n\nReview the exact changes below and approve to compile this version.\n\n"
        + diff_text
    )
    if len(text) > 3900:
        text = text[:3900].rstrip() + "\n...preview truncated..."
    await update.effective_message.reply_text(
        text,
        reply_markup=build_edit_preview_keyboard(session_id),
    )


async def process_one(
    url: str, role_focus: str | None, status_msg: Message | None
) -> tuple[str, dict, bytes | None, bytes | None]:
    domain = urlparse(url).netloc or url
    label = domain.split(".")[0].lower()
    logger.info(f"Processing URL: {url}")
    if status_msg is not None:
        await _safe_edit(status_msg, f"*{domain}*\nScraping job description...")
    try:
        description = await scrape_job_description(url)
    except httpx.HTTPError as exc:
        raise RuntimeError(_format_network_error(domain, exc)) from exc
    if len(description.strip()) < 20:
        raise RuntimeError(
            "Could not extract enough job description text from that page. "
            "Paste the description as `#cv ...` text or upload it as a `.txt` / `.md` file."
        )
    logger.info(f"Scraped {len(description)} characters from {url}")
    if status_msg is not None:
        await _safe_edit(status_msg, f"*{domain}*\nSubmitting job...")
    job_id = await submit_job(description, role_focus, label=label, source_url=url)
    metadata = await poll_job(job_id, status_msg, domain)
    pdf_bytes: bytes | None = None
    typ_bytes: bytes | None = None
    if metadata.get("pdf_url"):
        pdf_bytes = await download_pdf(metadata["pdf_url"])
        logger.info(f"Downloaded PDF ({len(pdf_bytes)} bytes) for job {job_id}")
    if metadata.get("typst_url"):
        typ_bytes = await download_typst(metadata["typst_url"])
        logger.info(f"Downloaded Typst ({len(typ_bytes)} bytes) for job {job_id}")
    return url, metadata, pdf_bytes, typ_bytes


async def process_uploaded_text(
    message: Message,
    role_focus: str | None,
    extra_text: str,
    status_msg: Message | None,
) -> tuple[str, dict, bytes | None, bytes | None]:
    filename, description = await read_uploaded_text(message)
    label = sanitize_label(filename)
    display = filename
    if extra_text:
        description = (
            f"{description}\n\n"
            f"--- USER NOTES AND REQUESTS ---\n"
            f"{extra_text}"
        )
    if status_msg is not None:
        await _safe_edit(status_msg, f"*{display}*\nSubmitting uploaded text...")
    job_id = await submit_job(description, role_focus, label=label)
    metadata = await poll_job(job_id, status_msg, display)
    pdf_bytes: bytes | None = None
    typ_bytes: bytes | None = None
    if metadata.get("pdf_url"):
        pdf_bytes = await download_pdf(metadata["pdf_url"])
    if metadata.get("typst_url"):
        typ_bytes = await download_typst(metadata["typst_url"])
    return filename, metadata, pdf_bytes, typ_bytes


async def process_direct_text(
    text: str,
    role_focus: str | None,
    status_msg: Message | None,
) -> tuple[str, dict, bytes | None, bytes | None]:
    label = "telegram-text"
    if status_msg is not None:
        await _safe_edit(status_msg, f"*{label}*\nSubmitting tagged text...")
    job_id = await submit_job(text, role_focus, label=label)
    metadata = await poll_job(job_id, status_msg, label)
    pdf_bytes: bytes | None = None
    typ_bytes: bytes | None = None
    if metadata.get("pdf_url"):
        pdf_bytes = await download_pdf(metadata["pdf_url"])
    if metadata.get("typst_url"):
        typ_bytes = await download_typst(metadata["typst_url"])
    return label, metadata, pdf_bytes, typ_bytes


# ── handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "*CV Docker Bot*\n\n"
        "*Generate:* Send one or more job posting URLs (one per line).\n"
        "Optional role prefix: `ai:`, `systems:`, `software:`, `architecture:`\n\n"
        "*Generate from tagged text message:* Start the message with `#cv`\n"
        "Optional first line or second line role hint: `#cv systems` or `role: systems`\n\n"
        "*Generate from text file:* Upload a `.txt` or `.md` file with the job description, your notes, and your requests.\n"
        "Optional caption: `systems`, `role: systems`, or any free-text notes/requests to append to the uploaded brief.\n\n"
        "*Edit a previous CV:*\n"
        "`/edit` — pick from ID list, preview diff, then approve\n"
        "`/edit <id> <instructions>` — direct preview\n\n"
        "*Score an existing CV without regenerating it:*\n"
        "`/score <id>` — score against the original job description\n"
        "`/score <id> <url>` — score against a freshly scraped job description from that URL\n"
        "Reply to a new job description text or `.txt` / `.md` file with `/score <id>` — score against the new description\n\n"
        "*Stop active jobs:*\n"
        "`/stop` — multi-select from active jobs\n"
        "`/stop <id>` — direct\n\n"
        "*Remove a CV:*\n"
        "`/remove` — multi-select from saved CVs\n"
        "`/remove <id>` — direct",
        parse_mode="Markdown",
    )


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    args = context.args or []
    if len(args) < 1:
        await update.message.reply_text(
            "*Usage:* `/score <id>`\n"
            "Scores the CV against its original job description.\n\n"
            "*New description options:*\n"
            "`/score <id> <url>` — scrape and score against that job posting\n"
            "Reply to a text message or `.txt` / `.md` file containing a new job description with `/score <id>`.",
            parse_mode="Markdown",
        )
        return

    job_id_arg = args[0]
    try:
        original, error = await resolve_original(job_id_arg, allowed_statuses=EDITABLE_JOB_STATUSES)
    except Exception as e:
        await update.message.reply_text(f"Lookup failed: `{e}`", parse_mode="Markdown")
        return
    if original is None:
        await update.message.reply_text(error or f"No completed CV found with id `{job_id_arg}`.", parse_mode="Markdown")
        return

    full_id = original["job_id"]
    label = original.get("extra", {}).get("label") or full_id[:8]
    replied = update.message.reply_to_message
    against_original = True
    job_description: str | None = None
    score_target = "original job description"

    command_tail = " ".join(args[1:]).strip()
    url_match = URL_RE.search(command_tail)
    if url_match:
        url = url_match.group(0)
        try:
            job_description = await scrape_job_description(url)
        except Exception as e:
            await update.message.reply_text(f"Could not scrape the URL for scoring: `{e}`", parse_mode="Markdown")
            return
        if len(job_description.strip()) < 20:
            await update.message.reply_text(
                "The scraped job description was too short. Send `/score <id> <url>` with a scraper-friendly posting, or reply with the full JD text / `.txt` / `.md` file.",
                parse_mode="Markdown",
            )
            return
        against_original = False
        score_target = f"new job description from {urlparse(url).netloc or url}"

    elif replied is not None:
        if replied.document is not None:
            try:
                _filename, job_description = await read_uploaded_text(replied)
            except Exception as e:
                await update.message.reply_text(f"Could not read replied file: `{e}`", parse_mode="Markdown")
                return
            against_original = False
            score_target = "new job description from replied file"
        else:
            candidate = _clean(replied.text or replied.caption or "")
            if len(candidate) < 20:
                await update.message.reply_text(
                    "The replied message is too short. Reply to a full job description text or a `.txt` / `.md` file."
                )
                return
            job_description = candidate
            against_original = False
            score_target = "new job description from replied text"

    status_msg = await update.message.reply_text(
        f"*{label}*\nScoring CV against {score_target}...",
        parse_mode="Markdown",
    )
    try:
        result = await request_score_resume(full_id, job_description)
    except Exception as e:
        await _safe_edit(status_msg, f"*{label}*\nScoring failed: `{e}`")
        return

    try:
        await status_msg.delete()
    except Exception:
        pass

    report = result.get("report", {})
    await update.message.reply_text(
        _score_report_message(
            report,
            label=full_id[:8],
            against_original=result.get("used_original_job_description", against_original),
        ),
        parse_mode="Markdown",
    )


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    args = context.args or []

    # No args → show inline keyboard to pick a CV
    if len(args) == 0:
        try:
            await send_cv_picker(update.message, context, "edit")
        except Exception as e:
            await update.message.reply_text(f"Could not fetch CV list: `{e}`", parse_mode="Markdown")
        return

    # With args → direct mode: first arg is id/label, rest are instructions
    if len(args) < 2:
        await update.message.reply_text(
            "*Usage:* `/edit <id> <instructions>`\n"
            "Or just `/edit` to pick from the ID list.",
            parse_mode="Markdown",
        )
        try:
            await send_cv_picker(update.message, context, "edit")
        except Exception:
            pass
        return

    label = args[0].lower()
    instructions = " ".join(args[1:])
    status_msg = await update.message.reply_text(
        f"*{label}*\nLooking up previous CV...", parse_mode="Markdown"
    )
    try:
        original, error = await resolve_original(label, allowed_statuses=EDITABLE_JOB_STATUSES)
    except Exception as e:
        await _safe_edit(status_msg, f"*{label}*\nLookup failed: `{e}`")
        return
    if original is None:
        await _safe_edit(status_msg, f"*{label}*\n{error or 'No CV found with that ID.'}")
        try:
            await send_cv_picker(update.message, context, "edit")
        except Exception:
            pass
        return
    await run_edit(update, context, original, instructions, status_msg)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    args = context.args or []

    if not args:
        try:
            await send_cv_picker(update.message, context, "stop")
        except Exception as e:
            await update.message.reply_text(f"Could not fetch active jobs: `{e}`", parse_mode="Markdown")
        return

    job_id_arg = args[0]
    status_msg = await update.message.reply_text(
        f"*{job_id_arg}*\nStopping job...", parse_mode="Markdown"
    )
    try:
        original, error = await resolve_original(job_id_arg, allowed_statuses=ACTIVE_JOB_STATUSES)
    except Exception as e:
        await _safe_edit(status_msg, f"*{job_id_arg}*\nLookup failed: `{e}`")
        return
    if original is None:
        await _safe_edit(status_msg, f"*{job_id_arg}*\n{error or 'No active job found with that ID.'}")
        try:
            await send_cv_picker(update.message, context, "stop")
        except Exception:
            pass
        return

    stopped, stop_error = await request_stop_job(original["job_id"])
    label = original.get("extra", {}).get("label") or original["job_id"][:8]
    if stopped is None:
        await _safe_edit(status_msg, f"*{label}*\nCould not stop job: {stop_error or 'Unknown error.'}")
        return
    await _safe_edit(status_msg, f"*{label}*\nStopped `{original['job_id'][:8]}`.", parse_mode="Markdown")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    args = context.args or []

    # No args → show inline keyboard
    if not args:
        try:
            await send_cv_picker(update.message, context, "remove")
        except Exception as e:
            await update.message.reply_text(f"Could not fetch CV list: `{e}`", parse_mode="Markdown")
        return

    # Direct: /remove <id>
    job_id_arg = args[0]
    try:
        original, error = await resolve_original(job_id_arg, allowed_statuses=REMOVABLE_JOB_STATUSES)
    except Exception as e:
        await update.message.reply_text(f"Lookup failed: `{e}`", parse_mode="Markdown")
        return
    if original is None:
        active_job, _active_error = await resolve_original(job_id_arg, allowed_statuses=ACTIVE_JOB_STATUSES)
        if active_job is not None:
            await update.message.reply_text(
                f"CV `{job_id_arg}` is still running. Use `/stop {active_job['job_id'][:8]}` first.",
                parse_mode="Markdown",
            )
            return
        await update.message.reply_text(error or f"No CV found with id `{job_id_arg}`.", parse_mode="Markdown")
        try:
            await send_cv_picker(update.message, context, "remove")
        except Exception:
            pass
        return
    full_id = original["job_id"]
    label = original.get("extra", {}).get("label") or full_id[:8]
    title = original.get("extra", {}).get("job_title", "")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, delete", callback_data=f"remove_confirm:{full_id}"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ]])
    await update.message.reply_text(
        f"Delete CV `{full_id[:8]}` — *{label}* {title}?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "cancel":
        await query.edit_message_text("Cancelled.")
        return

    if data.startswith("edit_preview_cancel:"):
        session_id = data.split(":", 1)[1]
        _drop_preview_session(context, session_id)
        await query.edit_message_text("Edit preview cancelled.")
        return

    if data.startswith("edit_preview_approve:"):
        session_id = data.split(":", 1)[1]
        session = _get_preview_session(context, session_id)
        if session is None:
            await query.edit_message_text("This edit preview expired. Run /edit again.")
            return
        _drop_preview_session(context, session_id)
        await query.edit_message_text("Approved. Compiling this exact edit...")
        status_msg = await query.message.reply_text("Preparing approved edit...")
        await run_approved_edit(
            context,
            query.message,
            session["original"],
            session["instructions"],
            session["preview"]["after_draft"],
            status_msg,
        )
        return

    if data.startswith("edit_retry_cancel:"):
        session_id = data.split(":", 1)[1]
        _drop_retry_session(context, session_id)
        await query.edit_message_text("One-page retry cancelled.")
        return

    if data.startswith("edit_retry_one_page:"):
        session_id = data.split(":", 1)[1]
        session = _get_retry_session(context, session_id)
        if session is None:
            await query.edit_message_text("This one-page retry expired. Run /edit again.")
            return
        _drop_retry_session(context, session_id)
        await query.edit_message_text("Preparing one-page retry preview...")
        status_msg = await query.message.reply_text("Preparing one-page retry preview...")
        merged_instructions = "\n\n".join(
            part for part in [
                (session.get("instructions") or "").strip(),
                AUTO_ONE_PAGE_EDIT_INSTRUCTIONS,
            ] if part
        )
        await run_edit(
            update,
            context,
            session["original"],
            merged_instructions,
            status_msg,
        )
        return

    if data.startswith("picker_cancel:"):
        session_id = data.split(":", 1)[1]
        _drop_picker_session(context, session_id)
        await query.edit_message_text("Cancelled.")
        return

    if data.startswith("picker_back:"):
        session_id = data.split(":", 1)[1]
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        await query.edit_message_text(
            _multi_picker_text(session),
            reply_markup=build_multi_cv_keyboard(session_id, session),
        )
        return

    if data.startswith("picker_toggle:"):
        _prefix, session_id, full_id = data.split(":", 2)
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        _toggle_picker_selection(session, full_id)
        await query.edit_message_text(
            _multi_picker_text(session),
            reply_markup=build_multi_cv_keyboard(session_id, session),
        )
        return

    if data.startswith("picker_select_all:"):
        session_id = data.split(":", 1)[1]
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        _set_picker_selection(session, [job["job_id"] for job in session["jobs"]])
        await query.edit_message_text(
            _multi_picker_text(session),
            reply_markup=build_multi_cv_keyboard(session_id, session),
        )
        return

    if data.startswith("picker_clear:"):
        session_id = data.split(":", 1)[1]
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        _set_picker_selection(session, [])
        await query.edit_message_text(
            _multi_picker_text(session),
            reply_markup=build_multi_cv_keyboard(session_id, session),
        )
        return

    if data.startswith("picker_stop_selected:"):
        session_id = data.split(":", 1)[1]
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        jobs = _selected_jobs(session)
        if not jobs:
            await query.message.reply_text("Select at least one job to stop.")
            return
        stopped: list[str] = []
        failed: list[str] = []
        for job in jobs:
            _result, error = await request_stop_job(job["job_id"])
            if error:
                failed.append(f"{job['job_id'][:8]} ({error})")
            else:
                stopped.append(job["job_id"][:8])
        _drop_picker_session(context, session_id)
        lines = []
        if stopped:
            lines.append("Stopped: " + ", ".join(f"`{job_id}`" for job_id in stopped))
        if failed:
            lines.append("Failed: " + ", ".join(failed))
        await query.edit_message_text("\n".join(lines) or "No jobs were stopped.", parse_mode="Markdown")
        return

    if data.startswith("picker_remove_selected:") or data.startswith("picker_remove_all:"):
        prefix, session_id = data.split(":", 1)
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        mode = "all" if prefix == "picker_remove_all" else "selected"
        jobs = session["jobs"] if mode == "all" else _selected_jobs(session)
        if not jobs:
            await query.message.reply_text("Select at least one CV to remove.")
            return
        await query.edit_message_text(
            _confirm_picker_text("remove", jobs, mode=mode),
            reply_markup=build_confirm_keyboard(session_id, mode),
        )
        return

    if data.startswith("picker_confirm_remove:"):
        _prefix, session_id, mode = data.split(":", 2)
        session = _get_picker_session(context, session_id)
        if session is None:
            await query.edit_message_text("This picker expired. Run the command again.")
            return
        jobs = session["jobs"] if mode == "all" else _selected_jobs(session)
        deleted: list[str] = []
        failed: list[str] = []
        for job in jobs:
            ok, error = await request_delete_job(job["job_id"])
            if ok:
                deleted.append(job["job_id"][:8])
            else:
                failed.append(f"{job['job_id'][:8]} ({error or 'unknown'})")
        _drop_picker_session(context, session_id)
        lines = []
        if deleted:
            lines.append("Deleted: " + ", ".join(f"`{job_id}`" for job_id in deleted))
        if failed:
            lines.append("Failed: " + ", ".join(failed))
        await query.edit_message_text("\n".join(lines) or "Nothing was deleted.", parse_mode="Markdown")
        return

    # ── remove: show confirm ──────────────────────────────────────────────────
    if data.startswith("remove:"):
        full_id = data.split(":", 1)[1]
        original = await fetch_job(full_id)
        if original is None:
            await query.edit_message_text(f"CV `{full_id[:8]}` not found.", parse_mode="Markdown")
            return
        label = original.get("extra", {}).get("label") or full_id[:8]
        title = original.get("extra", {}).get("job_title", "")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, delete", callback_data=f"remove_confirm:{full_id}"),
            InlineKeyboardButton("Cancel", callback_data="cancel"),
        ]])
        await query.edit_message_text(
            f"Delete CV `{full_id[:8]}` — *{label}* {title}?",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return

    # ── remove_confirm: do the delete ─────────────────────────────────────────
    if data.startswith("remove_confirm:"):
        full_id = data.split(":", 1)[1]
        try:
            ok, error = await request_delete_job(full_id)
            if ok:
                await query.edit_message_text(f"Deleted CV `{full_id[:8]}`.", parse_mode="Markdown")
            else:
                await query.edit_message_text(
                    f"Could not delete `{full_id[:8]}`: {error or 'Unknown error.'}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            await query.edit_message_text(f"Error: `{e}`", parse_mode="Markdown")
        return

    # ── edit_select: store pending state, ask for instructions ────────────────
    if data.startswith("edit_select:"):
        full_id = data.split(":", 1)[1]
        context.user_data["pending_edit_job_id"] = full_id
        original = await fetch_job(full_id)
        label = ""
        if original:
            label = original.get("extra", {}).get("label") or full_id[:8]
            title = original.get("extra", {}).get("job_title", "")
            context.user_data["pending_edit_original"] = original
            display = f"`{full_id[:8]}` — *{label}* {title}"
        else:
            display = f"`{full_id[:8]}`"
        await query.edit_message_text(
            f"Selected {display}\n\nNow send your edit instructions as a reply. I will show a diff preview before compiling.\nExample: `bold Python in the first bullet`",
            parse_mode="Markdown",
        )
        return


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        logger.warning(f"Unauthorized access attempt from user {update.effective_user.id}")
        return

    text = update.message.text or ""

    # ── pending edit: treat message as instructions ───────────────────────────
    if context.user_data.get("pending_edit_job_id"):
        full_id = context.user_data.pop("pending_edit_job_id")
        original = context.user_data.pop("pending_edit_original", None)
        if original is None:
            original = await fetch_job(full_id)
        if original is None:
            await update.message.reply_text(f"CV `{full_id[:8]}` not found.", parse_mode="Markdown")
            return
        label = original.get("extra", {}).get("label") or full_id[:8]
        status_msg = await update.message.reply_text(
            f"*{label}*\nApplying edits...", parse_mode="Markdown"
        )
        await run_edit(update, context, original, text.strip(), status_msg)
        return

    # ── URL generation flow ───────────────────────────────────────────────────
    if update.message.document:
        role_focus, extra_text = parse_caption_payload(update.message.caption or "")
        filename = update.message.document.file_name or "upload.txt"
        status_msg = await update.message.reply_text(
            f"*{filename}*\nQueued uploaded text...",
            parse_mode="Markdown",
        )
        try:
            _name, metadata, pdf_bytes, typ_bytes = await process_uploaded_text(
                update.message,
                role_focus,
                extra_text,
                status_msg,
            )
        except Exception as exc:
            try:
                await status_msg.delete()
            except Exception:
                pass
            await update.message.reply_text(
                f"{filename}\nError: `{exc}`",
                parse_mode="Markdown",
            )
            return

        try:
            await status_msg.delete()
        except Exception:
            pass

        if metadata["status"] == "completed" and pdf_bytes:
            short_id = metadata.get("job_id", "")[:8]
            job_title = metadata.get("extra", {}).get("job_title", "")
            label = metadata.get("extra", {}).get("label") or sanitize_label(filename)
            caption_parts = [f"`{short_id}` — {job_title}" if job_title else f"`{short_id}` — {label}"]
            if metadata.get("fit_summary"):
                caption_parts.append(metadata["fit_summary"])
            if metadata.get("keywords"):
                caption_parts.append("Keywords: " + ", ".join(metadata["keywords"][:10]))
            caption = "\n\n".join(caption_parts)[:1024]
            await update.message.reply_document(
                document=pdf_bytes,
                filename=OUTPUT_PDF_FILENAME,
                caption=caption,
                parse_mode="Markdown",
            )
            if typ_bytes is not None:
                await update.message.reply_document(
                    document=typ_bytes,
                    filename=OUTPUT_TYPST_FILENAME,
                )
            score_text = _score_findings_text(metadata)
            if score_text:
                await update.message.reply_text(score_text)
        elif metadata["status"] == "stopped":
            await update.message.reply_text(f"{filename}\nStopped.", parse_mode="Markdown")
        else:
            error = metadata.get("error", "Unknown error")
            compile_logs = metadata.get("compile_logs", [])
            error_text = f"{filename}\nFailed: {error}"
            if compile_logs:
                error_text += f"\n\nCompile log:\n```\n{compile_logs[-1][:400]}\n```"
            await update.message.reply_text(error_text, parse_mode="Markdown")
        return

    tagged_request = parse_tagged_text_request(text)
    if tagged_request is not None:
        role_focus, body = tagged_request
        if len(body) < 20:
            await update.message.reply_text(
                f"{TEXT_GENERATION_TAG} message is too short. Put the job description, your notes, and your requests after the tag."
            )
            return

        status_msg = await update.message.reply_text(
            "*telegram-text*\nQueued tagged text...",
            parse_mode="Markdown",
        )
        try:
            label, metadata, pdf_bytes, typ_bytes = await process_direct_text(
                body,
                role_focus,
                status_msg,
            )
        except Exception as exc:
            try:
                await status_msg.delete()
            except Exception:
                pass
            await update.message.reply_text(
                f"telegram-text\nError: `{exc}`",
                parse_mode="Markdown",
            )
            return

        try:
            await status_msg.delete()
        except Exception:
            pass

        if metadata["status"] == "completed" and pdf_bytes:
            short_id = metadata.get("job_id", "")[:8]
            job_title = metadata.get("extra", {}).get("job_title", "")
            caption_parts = [f"`{short_id}` — {job_title}" if job_title else f"`{short_id}` — {label}"]
            if metadata.get("fit_summary"):
                caption_parts.append(metadata["fit_summary"])
            if metadata.get("keywords"):
                caption_parts.append("Keywords: " + ", ".join(metadata["keywords"][:10]))
            caption = "\n\n".join(caption_parts)[:1024]
            await update.message.reply_document(
                document=pdf_bytes,
                filename=OUTPUT_PDF_FILENAME,
                caption=caption,
                parse_mode="Markdown",
            )
            if typ_bytes is not None:
                await update.message.reply_document(
                    document=typ_bytes,
                    filename=OUTPUT_TYPST_FILENAME,
                )
            score_text = _score_findings_text(metadata)
            if score_text:
                await update.message.reply_text(score_text)
        elif metadata["status"] == "stopped":
            await update.message.reply_text("telegram-text\nStopped.", parse_mode="Markdown")
        else:
            error = metadata.get("error", "Unknown error")
            compile_logs = metadata.get("compile_logs", [])
            error_text = f"telegram-text\nFailed: {error}"
            if compile_logs:
                error_text += f"\n\nCompile log:\n```\n{compile_logs[-1][:400]}\n```"
            await update.message.reply_text(error_text, parse_mode="Markdown")
        return

    jobs = parse_message(text)
    if not jobs:
        await update.message.reply_text(
            f"No URLs detected. Send job posting links, upload a `.txt` / `.md` file, or start a message with `{TEXT_GENERATION_TAG}`."
        )
        return

    n = len(jobs)
    status_msgs: list[Message] = []
    for url, _role in jobs:
        domain = urlparse(url).netloc or url
        msg = await update.message.reply_text(f"*{domain}*\nQueued...", parse_mode="Markdown")
        status_msgs.append(msg)

    tasks = [
        process_one(url, role, smsg)
        for (url, role), smsg in zip(jobs, status_msgs)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, ((url, _role), result, smsg) in enumerate(zip(jobs, results, status_msgs), start=1):
        domain = urlparse(url).netloc or url
        label = f"[{idx}/{n}] {domain}"
        try:
            await smsg.delete()
        except Exception:
            pass
        if isinstance(result, Exception):
            logger.error("Error processing %s: %s", url, result)
            await update.message.reply_text(f"{label}\nError: `{result}`", parse_mode="Markdown")
            continue
        _url, metadata, pdf_bytes, typ_bytes = result
        if metadata["status"] == "completed" and pdf_bytes:
            short_id = metadata.get("job_id", "")[:8]
            job_title = metadata.get("extra", {}).get("job_title", "")
            caption_parts = [f"`{short_id}` — {job_title}" if job_title else f"`{short_id}` — {domain}"]
            if metadata.get("fit_summary"):
                caption_parts.append(metadata["fit_summary"])
            if metadata.get("keywords"):
                caption_parts.append("Keywords: " + ", ".join(metadata["keywords"][:10]))
            caption = "\n\n".join(caption_parts)[:1024]
            await update.message.reply_document(
                document=pdf_bytes,
                filename=OUTPUT_PDF_FILENAME,
                caption=caption,
                parse_mode="Markdown",
            )
            if typ_bytes is not None:
                await update.message.reply_document(
                    document=typ_bytes,
                    filename=OUTPUT_TYPST_FILENAME,
                )
            score_text = _score_findings_text(metadata)
            if score_text:
                await update.message.reply_text(score_text)
        elif metadata["status"] == "stopped":
            await update.message.reply_text(f"{label}\nStopped.", parse_mode="Markdown")
        else:
            error = metadata.get("error", "Unknown error")
            compile_logs = metadata.get("compile_logs", [])
            error_text = f"{label}\nFailed: {error}"
            if compile_logs:
                error_text += f"\n\nCompile log:\n```\n{compile_logs[-1][:400]}\n```"
            await update.message.reply_text(error_text, parse_mode="Markdown")


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started with API_URL=%s", API_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
