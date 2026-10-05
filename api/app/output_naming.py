"""Deterministic employer detection for CV download filenames."""
from __future__ import annotations

import re
from urllib.parse import urlparse


GENERIC_COMPANY_NAMES = {
    "apply", "boards", "careers", "comeet", "company", "greenhouse", "indeed",
    "jobs", "job", "job boards", "lever", "linkedin", "myworkdayjobs",
    "organization", "workable", "workday", "secrethunter", "ashbyhq",
    "smartrecruiters", "icims", "jobvite", "bamboohr", "recruitee",
    "teamtailor", "taleo", "successfactors", "github", "www", "us",
    "team", "the team", "our team", "role", "the role", "the job",
    "the company", "position", "the position",
}

# Use the public employer brand on these first-party career sites, including
# Amazon's .jobs domain and postings for its subsidiary Annapurna Labs.
OFFICIAL_CAREER_DOMAINS = {
    "apple.com": "Apple",
    "amazon.jobs": "Amazon",
    "amazon.com": "Amazon",
}


def short_output_company(company: str) -> str:
    """Keep at most two employer words, excluding a leading article."""
    company = re.sub(r"^(?:the|a|an)[\s_-]+", "", company.strip(), flags=re.I)
    # Count filename-safe words: punctuation must not turn two words into
    # three or more underscore-separated tokens when the filename is sanitized.
    words = re.findall(r"[^\W_]+", company)
    return " ".join(words[:2])


def _candidate(value: str) -> str:
    company = value.strip(" .:|-_")
    normalized = re.sub(r"[-_]", " ", company).strip().lower()
    if not company or normalized in GENERIC_COMPANY_NAMES:
        return ""
    if len(company) > 60 or not re.fullmatch(r"[\w&.' -]+", company):
        return ""
    return company


def infer_output_company(job_description: str, source_url: str = "") -> str:
    """Prefer explicit employer fields, then official sites and visible headings.

    Other employer domains are a fallback. Generic job-board hosts and labels
    never become an employer name, and no model call is needed.
    """
    lines = [line.strip() for line in job_description.splitlines()[:100] if line.strip()]
    for line in lines:
        match = re.match(r"^(?:company|employer|organization)\s*:\s*(.+)$", line, re.I)
        if match and (company := _candidate(match.group(1))):
            return company

    try:
        parsed = urlparse(source_url)
        host = (parsed.hostname or "").rstrip(".").lower() if parsed.scheme in {"http", "https"} else ""
    except ValueError:
        host = ""
    for domain, company in OFFICIAL_CAREER_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return company

    patterns = (
        r"^about\s+(.+?)\s*$",
        r"^(.+?)\s+(?:is|are)\s+(?:seeking|hiring|looking for)\b",
        r"^.+?\s+[—–]\s+(.+?)\s*$",
    )
    for line in lines:
        for pattern in patterns:
            match = re.match(pattern, line, re.I)
            if match and (company := _candidate(match.group(1))):
                return company

    parts = host.split(".")
    if len(parts) < 2 or all(part.isdigit() for part in parts):
        return ""
    suffix_pairs = {("co", "il"), ("co", "uk"), ("com", "au"), ("co", "jp"), ("com", "br")}
    index = -3 if len(parts) >= 3 and tuple(parts[-2:]) in suffix_pairs else -2
    company = _candidate(parts[index])
    return company.replace("-", " ").replace("_", " ").title() if company else ""
