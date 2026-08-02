#!/usr/bin/env python3
"""Fail CI when tracked files contain credentials or private profile data.

Optional GitHub secret: LEAK_CHECK_TERMS
Set it to newline-separated personal values that must never be committed
(for example, a real email address, phone number, full name, or home address).
The script reports only the file path, never the matched value.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from math import log2
from pathlib import Path, PurePosixPath


SAFE_ENV_FILES = {".env.example", ".env.stateful.production.example"}
BLOCKED_PATH_PREFIXES = (
    "data/profile/",
    "data/profiles/",
    "data/now/",
    "data.prod/",
    "outputs.prod/",
)
BLOCKED_OUTPUT_PREFIX = "outputs/"
SAFE_OUTPUT_FILES = {"outputs/.gitkeep"}

TOKEN_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AQ\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)"
    r"[ \t]*=[ \t]*[\"']?([^\s#\"']+)"
)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
CONTACT_LINE_PATTERN = re.compile(
    r"(?im)^[ \t]*(?:[-*][ \t]*)?(?:phone|mobile|tel|whatsapp)[ \t]*[:=|].*$"
)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[\d .()\-]{6,}\d)(?!\d)")
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "localhost", "invalid", "test"}


def repository_files() -> list[str]:
    # Include untracked, non-ignored release candidates during local audits.
    # In CI, the same command naturally resolves to the committed file set.
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    return [path for path in output.decode("utf-8").split("\0") if path]


def is_blocked_path(path: str) -> bool:
    if path in SAFE_ENV_FILES:
        return False
    name = PurePosixPath(path).name
    if name == ".env" or (name.startswith(".env.") and path not in SAFE_ENV_FILES):
        return True
    if path.startswith(BLOCKED_PATH_PREFIXES):
        return True
    return path.startswith(BLOCKED_OUTPUT_PREFIX) and path not in SAFE_OUTPUT_FILES


def read_text(path: str) -> str | None:
    raw = Path(path).read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def configured_terms() -> list[str]:
    # GitHub masks secret values in logs. Never include these terms in output.
    return [term.casefold().strip() for term in os.getenv("LEAK_CHECK_TERMS", "").splitlines() if len(term.strip()) >= 3]


def has_non_placeholder_secret_assignment(text: str) -> bool:
    safe_fragments = ("${", "your", "replace", "example", "placeholder", "changeme", "dummy", "os.getenv", "getenv", "process.env")
    for match in ASSIGNMENT_PATTERN.finditer(text):
        value = match.group(1).casefold()
        entropy = -sum((count / len(value)) * log2(count / len(value)) for count in Counter(value).values()) if value else 0
        if (
            len(value) >= 16
            and any(char.isdigit() for char in value)
            and entropy >= 3.5
            and not any(fragment in value for fragment in safe_fragments)
        ):
            return True
    return False


def audit() -> list[str]:
    findings: list[str] = []
    terms = configured_terms()
    for path in repository_files():
        if is_blocked_path(path):
            findings.append(f"private-profile-or-output path is tracked: {path}")
            continue
        text = read_text(path)
        if text is None:
            continue
        if any(pattern.search(text) for pattern in TOKEN_PATTERNS) or has_non_placeholder_secret_assignment(text):
            findings.append(f"credential-shaped value found: {path}")
        for email in EMAIL_PATTERN.finditer(text):
            if email.group(1).casefold() not in SAFE_EMAIL_DOMAINS:
                findings.append(f"non-placeholder email address found: {path}")
                break
        for line in CONTACT_LINE_PATTERN.findall(text):
            if any(len(re.sub(r"\D", "", value)) >= 8 for value in PHONE_PATTERN.findall(line)):
                findings.append(f"phone-like contact value found: {path}")
                break
        normalized = text.casefold()
        if any(term in normalized for term in terms):
            findings.append(f"configured personal-data term found: {path}")
    return findings


def main() -> int:
    findings = audit()
    if not findings:
        print("Privacy audit passed: no tracked private-profile paths, credential patterns, or configured personal terms found.")
        return 0
    print("Privacy audit failed. Remove the sensitive content before pushing:", file=sys.stderr)
    for finding in sorted(set(findings)):
        print(f"- {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
