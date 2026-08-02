from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from app.renderer import escape_typst


CLOSINGS = {
    "sincerely",
    "sincerely yours",
    "best regards",
    "kind regards",
    "regards",
    "respectfully",
}


GENERIC_COMPANY_NAMES = {
    "apply",
    "boards",
    "careers",
    "comeet",
    "company",
    "greenhouse",
    "indeed",
    "jobs",
    "job boards",
    "lever",
    "linkedin",
    "myworkdayjobs",
    "organization",
    "workable",
    "workday",
}


def infer_cover_letter_company(job_description: str, source_url: str, label: str) -> str:
    """Find the employer name without mistaking an ATS host/label for the employer."""
    lines = [line.strip() for line in job_description.splitlines()[:100] if line.strip()]
    for line in lines:
        match = re.match(r"^(?:company|employer|organization)\s*:\s*(.+)$", line, re.I)
        if match:
            company = match.group(1).strip(" .:|-")
            if company and company.lower() not in GENERIC_COMPANY_NAMES:
                return company[:60]

    # Job boards commonly put the real employer in a heading such as "About Bluevine".
    # Prefer that visible job-description evidence to a generic ATS hostname or scraper label.
    for line in lines:
        match = re.match(r"^about\s+(.+?)\s*$", line, re.I)
        if not match:
            continue
        company = match.group(1).strip(" .:|-")
        if company.lower() not in {"job", "role", "team", "the job", "the role", "us"}:
            return company[:60]

    parsed_url = urlparse(str(source_url))
    host_parts = [part for part in (parsed_url.hostname or "").split(".") if part]
    suffix_pairs = {("co", "il"), ("co", "uk"), ("com", "au")}
    if len(host_parts) >= 3 and tuple(host_parts[-2:]) in suffix_pairs:
        candidate = host_parts[-3]
    elif len(host_parts) >= 2:
        candidate = host_parts[-2]
    else:
        candidate = ""

    # Greenhouse/Lever URLs encode the employer in the first path segment.
    if candidate.lower() in GENERIC_COMPANY_NAMES:
        path_parts = [part for part in parsed_url.path.split("/") if part]
        if path_parts and path_parts[0].lower() not in {"jobs", "job", "careers"}:
            candidate = path_parts[0]
        else:
            candidate = label
    if not candidate or candidate.lower().replace("-", " ") in GENERIC_COMPANY_NAMES:
        return "Company"
    words = re.sub(r"[-_]", " ", candidate).split()
    return " ".join(word.upper() if len(word) <= 4 else word.capitalize() for word in words)[:60]


def cover_letter_company_edit(instructions: str) -> str | None:
    """Extract an explicit company/sidebar replacement from a natural-language edit."""
    normalized = " ".join(instructions.split())
    context = normalized.lower()
    if not any(term in context for term in ("company", "hiring manager", "left", "sidebar")):
        return None
    if not any(term in context for term in ("change", "chage", "cahnge", "replace", "rename")):
        return None

    # Accept noisy Telegram phrasing, e.g. "change: JOB Boards - to Bluevine".
    matches = list(re.finditer(r"\bto\s+([\w&.'-]+(?:\s+[\w&.'-]+){0,5})", normalized, re.I))
    if not matches:
        matches = list(re.finditer(r"\bwith\s+([\w&.'-]+(?:\s+[\w&.'-]+){0,5})", normalized, re.I))
    if not matches:
        return None
    company = matches[-1].group(1).strip(" .,:;-_")
    return company[:60] or None


def is_header_only_cover_letter_edit(instructions: str, company_edit: str | None) -> bool:
    """Return true when an edit explicitly targets only the rendered sidebar company."""
    if not company_edit:
        return False
    lowered = instructions.lower()
    body_terms = (
        "body",
        "paragraph",
        "salutation",
        "opening",
        "closing",
        "sentence",
        "experience",
        "project",
        "wording",
        "also change",
        "also replace",
    )
    return not any(term in lowered for term in body_terms)


def split_cover_letter(content: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in content.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    salutation = "Dear Hiring Team,"
    if lines and re.match(r"^(dear|to\s+the)\b", lines[0], re.IGNORECASE):
        salutation = lines.pop(0)

    closing_index: int | None = None
    for index, line in enumerate(lines):
        normalized = line.rstrip(",.: ").lower()
        if normalized in CLOSINGS:
            closing_index = index
            break
    if closing_index is not None:
        lines = lines[:closing_index]

    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return salutation, paragraphs


def _body_typst(paragraphs: list[str], paragraph_gap_mm: float) -> str:
    if not paragraphs:
        return "#par[Thank you for considering my application.]"
    rendered = []
    for paragraph in paragraphs:
        rendered.append(f"#par[{escape_typst(paragraph)}]")
    return f"\n    #v({paragraph_gap_mm:.1f}mm)\n    ".join(rendered)


def compact_cover_letter(content: str, max_words: int = 200) -> str:
    """Shorten an overlong model response while preserving its opening and conclusion."""
    salutation, paragraphs = split_cover_letter(content)
    if sum(len(paragraph.split()) for paragraph in paragraphs) <= max_words:
        return content
    if not paragraphs:
        return content

    if len(paragraphs) == 1:
        words = paragraphs[0].split()[:max_words]
        shortened = " ".join(words)
        sentence_end = max(shortened.rfind("."), shortened.rfind("!"), shortened.rfind("?"))
        if sentence_end >= len(shortened) // 2:
            shortened = shortened[: sentence_end + 1]
        else:
            shortened = shortened.rstrip(" ,;:") + "."
        return f"{salutation}\n\n{shortened}\n\nSincerely,"

    conclusion = paragraphs[-1]
    conclusion_words = conclusion.split()
    if len(conclusion_words) > max_words // 3:
        conclusion_words = conclusion_words[: max_words // 3]
        conclusion = " ".join(conclusion_words).rstrip(" ,;:") + "."

    remaining = max_words - len(conclusion_words)
    kept: list[str] = []
    for paragraph in paragraphs[:-1]:
        words = paragraph.split()
        if len(words) <= remaining:
            kept.append(paragraph)
            remaining -= len(words)
            continue
        if remaining >= 12:
            shortened = " ".join(words[:remaining])
            sentence_end = max(shortened.rfind("."), shortened.rfind("!"), shortened.rfind("?"))
            if sentence_end >= len(shortened) // 2:
                shortened = shortened[: sentence_end + 1]
            else:
                shortened = shortened.rstrip(" ,;:") + "."
            kept.append(shortened)
        break
    kept.append(conclusion)
    return f"{salutation}\n\n" + "\n\n".join(kept) + "\n\nSincerely,"


def render_cover_letter(
    template: str,
    content: str,
    contact: dict[str, str],
    *,
    portrait_filename: str | None,
    company: str = "Company",
    recipient: str = "Hiring Manager",
    letter_date: date | None = None,
    body_font_size: float = 9.7,
    paragraph_gap_mm: float = 3.2,
) -> str:
    name = contact.get("name", "Candidate").strip() or "Candidate"
    phone = contact.get("tel", contact.get("phone", "")).strip()
    phone_digits = re.sub(r"\D", "", phone)
    if len(phone_digits) == 10 and phone_digits.startswith("0"):
        phone = f"{phone_digits[:3]}-{phone_digits[3:]}"
    salutation, paragraphs = split_cover_letter(content)
    initials = "".join(part[0] for part in name.split()[:2] if part).upper() or "CV"
    portrait = (
        f'#image("{portrait_filename}", width: 54mm, height: 54mm, fit: "cover")'
        if portrait_filename
        else f'#align(center + horizon)[#text(size: 18pt, weight: "bold")[{escape_typst(initials)}]]'
    )
    values = {
        "{{NAME}}": escape_typst(name),
        "{{EMAIL}}": escape_typst(contact.get("email", "")),
        "{{PHONE}}": escape_typst(phone),
        "{{LOCATION}}": escape_typst(contact.get("location", "")),
        "{{RECIPIENT}}": escape_typst(recipient),
        "{{COMPANY}}": escape_typst(company),
        "{{DATE}}": (letter_date or date.today()).strftime("%d.%m.%y"),
        "{{SALUTATION}}": escape_typst(salutation),
        "{{BODY}}": _body_typst(paragraphs, paragraph_gap_mm),
        "{{BODY_FONT_SIZE}}": f"{body_font_size:.1f}",
        "{{SIGNATURE_NAME}}": escape_typst(name),
        "{{PORTRAIT}}": portrait,
    }
    source = template
    for placeholder, value in values.items():
        source = source.replace(placeholder, value)
    return source
