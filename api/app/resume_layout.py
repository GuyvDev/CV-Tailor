from __future__ import annotations

import re
from pathlib import Path


MANAGED_LAYOUT_MARKER = "// cv-docker: managed-resume-layout-v1"
LAYOUT_DENSITIES = ("spacious", "comfortable", "compact")
_DENSITY_RULE = re.compile(r'^#let resume-density = "(spacious|comfortable|compact)"$', re.MULTILINE)


def default_resume_template() -> str:
    return Path(__file__).with_name("resume_template.typ").read_text(encoding="utf-8")


def resume_layout_variants(source: str):
    """Try the requested spacing, then tighter presets, without changing text."""
    density = _DENSITY_RULE.search(source)
    if MANAGED_LAYOUT_MARKER not in source or density is None:
        yield source
        return
    for preset in LAYOUT_DENSITIES[LAYOUT_DENSITIES.index(density[1]):]:
        yield source[:density.start(1)] + preset + source[density.end(1):]
