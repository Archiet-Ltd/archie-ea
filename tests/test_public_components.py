"""One button, card, table, FAQ and stepper for the public site.

The homepage and the pricing page both use the shared `.public-*` classes from css/public_site.css; the
pricing stylesheet keeps only layout that is specific to the page. These tests read the rendered pages and
the source tree, so a second copy of a component fails here as well as in the reuse-register gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
PRICING_CSS = APP / "static" / "css" / "public_pricing.css"

SHARED = ("public-faq", "public-card", "public-table", "public-btn--primary")
RETIRED = tuple(f"{'pp'}-{name}" for name in ("btn", "card", "table", "faq", "grow", "link"))


@pytest.fixture
def pages(app, client):
    out = {}
    for path in ("/", "/pricing"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        out[path] = resp.get_data(as_text=True)
    return out


@pytest.mark.parametrize("path", ["/", "/pricing"])
@pytest.mark.parametrize("cls", SHARED)
def test_both_pages_render_the_shared_component(pages, path, cls):
    assert re.search(r'class="[^"]*\b' + re.escape(cls) + r"\b", pages[path]), (
        f"{path} has no {cls}"
    )


def test_the_pricing_page_opts_into_the_marketing_palette(pages):
    body = re.search(r'<body\s+class="([^"]*)"', pages["/pricing"])
    assert body and "theme-marketing" in body.group(1).split()


def test_the_pricing_roadmap_is_the_shared_stepper(pages):
    assert re.search(r'<ol class="public-stepper">', pages["/pricing"])
    assert pages["/pricing"].count("public-stepper__body") == 4


@pytest.mark.parametrize("path", ["/", "/pricing"])
def test_every_summary_carries_the_chevron(pages, path):
    summaries = re.findall(r"<summary[^>]*>(.*?)</summary>", pages[path], flags=re.S)
    assert summaries
    assert all("<svg" in s and 'd="m6 9 6 6 6-6"' in s for s in summaries)


def test_no_file_under_app_uses_a_retired_pricing_class():
    found = []
    for path in APP.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".css", ".py", ".js", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in RETIRED:
            if re.search(r"(?<![\w-])" + re.escape(name) + r"\b", text):
                found.append(f"{path.relative_to(REPO).as_posix()}: {name}")
    assert found == []


def _rules(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body.strip()) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_the_pricing_stylesheet_restyles_no_shared_component():
    for sel, body in _rules(PRICING_CSS.read_text(encoding="utf-8")):
        assert ".public-btn" not in sel, f"pricing stylesheet styles the shared button: {sel}"
        assert ".public-faq" not in sel, f"pricing stylesheet styles the shared FAQ: {sel}"
        if any(re.search(r"\.public-card$", part.strip()) for part in sel.split(",")):
            props = {d.split(":")[0].strip() for d in body.split(";") if d.strip()}
            assert props <= {"display", "flex-direction"}, (
                f"pricing stylesheet restyles the shared card: {sel}"
            )
    # The card title and body keep the shared type: no font or padding is set on them from this page.
    for sel, body in _rules(PRICING_CSS.read_text(encoding="utf-8")):
        if "public-card-title" in sel or "public-card-body" in sel:
            props = {d.split(":")[0].strip() for d in body.split(";") if d.strip()}
            assert props <= {"margin", "margin-bottom"}, sel


SITE_CSS = APP / "static" / "css" / "public_site.css"
PRICING_HTML = APP / "templates" / "public" / "pricing.html"


def test_plan_and_service_grid_tracks_cannot_be_widened_by_their_content():
    css = PRICING_CSS.read_text(encoding="utf-8")
    assert "repeat(4, minmax(0, 1fr))" in css and "repeat(2, minmax(0, 1fr))" in css
    assert not re.search(r"repeat\(\d, 1fr\)", css)


def test_the_wrapping_button_shares_the_callout_declaration_block():
    css = SITE_CSS.read_text(encoding="utf-8")
    block = re.search(
        r"\.public-site \.public-card--callout \.public-btn,\s*\.public-site \.public-btn--wrap \{([^{}]*)\}", css)
    assert block and "white-space: normal" in block.group(1)
    assert css.count("white-space: normal") == 1


def test_every_plan_card_button_wraps_and_band_buttons_do_not():
    html = PRICING_HTML.read_text(encoding="utf-8")
    tags = re.findall(r'<a [^>]*?class="(public-btn[^"]*)"[^>]*?data-testid="(buy-[^"]*)"', html, flags=re.S)
    assert len(tags) == 5 and all("public-btn--wrap" in cls for cls, _ in tags)
    others = re.findall(r'class="(public-btn[^"]*)"[^>]*?data-testid="selfhost-github"', html)
    assert others and all("public-btn--wrap" not in cls for cls in others)


def test_the_comparison_frame_clips_its_corners_without_scrolling():
    assert re.search(r"\.pricing-page \.pp-frame \{ overflow: clip; \}", PRICING_CSS.read_text(encoding="utf-8"))
    assert 'class="pp-frame rounded-2xl border border-border"' in PRICING_HTML.read_text(encoding="utf-8")
