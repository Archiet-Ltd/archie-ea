"""T-L1-IMPORT-OPS: the product must never claim operational intelligence.

The release scorecard found the product has no operational data source (no
telemetry, incident, or SLA feed), so any claim of "operational
intelligence" — in a template, in the marketing content served from
content/pages/, or anywhere else a persona or a visitor reads — promises
something the product cannot deliver. This is a static, source-level check
(no app boot, no database) so it catches the phrase wherever it is written,
not only in the pages a particular test happens to render.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_PATTERNS = [
    re.compile(r"operational\s+intelligence", re.IGNORECASE),
    re.compile(r"operations\s+intelligence", re.IGNORECASE),
]


def _scan(paths, patterns):
    offenders = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break
    return offenders


def test_no_operational_intelligence_claim_in_templates():
    templates_dir = REPO_ROOT / "app" / "templates"
    offenders = _scan(templates_dir.rglob("*.html"), FORBIDDEN_PATTERNS)
    assert not offenders, (
        "templates claiming operational intelligence the product does not have: %r" % offenders
    )


def test_no_operational_intelligence_claim_in_public_content_pages():
    content_dir = REPO_ROOT / "content" / "pages"
    offenders = _scan(content_dir.rglob("*.md"), FORBIDDEN_PATTERNS)
    assert not offenders, (
        "public content pages claiming operational intelligence the product does not have: %r" % offenders
    )
