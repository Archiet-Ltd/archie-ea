"""The per-call Markdown helper must render exactly what the old shared instance did."""

from __future__ import annotations

from pathlib import Path

import markdown
import pytest

from app.services import public_pages

_ROOT = Path(public_pages.__file__).resolve().parents[2] / "content" / "pages"
_FILES = [
    _ROOT / "connectors" / "ai-assistants.md",
    _ROOT / "connectors" / "single-sign-on.md",
    _ROOT / "site" / "about.md",
]


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_helper_output_matches_old_singleton(path):
    assert path.exists(), path
    _front, body = public_pages._parse_front_matter(path.read_text(encoding="utf-8"))
    old_singleton = markdown.Markdown(extensions=["extra"])
    old_html = public_pages._sanitize_html(old_singleton.reset().convert(body))
    new_html = public_pages._sanitize_html(public_pages._render_markdown(body))
    assert new_html == old_html
    assert new_html.strip()
