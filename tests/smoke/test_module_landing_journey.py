"""Every /modules/<slug> page is the home page's design: a signed-out visitor reads what the product does,
the questions it answers and who asks them, the steps, sees the product (recording or screen) and can start.

Goal of the visitor: "decide, from this page alone, whether this module answers a question I am asked".
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from app.services.public_pages import CONTENT_ROOT, MERGED_PAGES, MODULE_CAPTURES

pytestmark = pytest.mark.journey

SLUGS = sorted(p.stem for p in (Path(CONTENT_ROOT) / "modules").glob("*.md"))
PROOF_PAGES = ("applications", "digital-twin", "ai-chat")
DATES = re.compile(r"\bQ[1-4]\s*(?:20)?\d\d\b|\bwave\s*\d\b|\b20(?:2[6-9]|3\d)\b", re.I)
CAPTURED = {entry[0] for entry in MODULE_CAPTURES}
MERGED = {slug for slug in SLUGS if f"/modules/{slug}" in MERGED_PAGES}
SHOTS = Path(os.environ.get("SMOKE_WALK_DIR") or Path(tempfile.gettempdir()) / "module-landing-shots")


@pytest.fixture(scope="module")
def visitor(browser, live_server):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    yield context, str(live_server).rstrip("/")
    context.close()


@pytest.mark.parametrize("slug", sorted(MERGED))
def test_a_merged_module_lands_on_its_parent_page_with_content(visitor, slug):
    context, base = visitor
    page = context.new_page()
    try:
        page.goto(f"{base}/modules/{slug}", wait_until="domcontentloaded")
        assert f"/modules/{slug}" not in page.url, "the old address sends the visitor on"
        assert page.locator("h1").first.inner_text().strip()
        assert len(page.locator("main").inner_text().split()) > 100, "the parent page has content"
    finally:
        page.close()


@pytest.mark.parametrize("slug", [s for s in SLUGS if s not in MERGED])
def test_module_page_is_the_landing_design(visitor, slug):
    context, base = visitor
    page = context.new_page()
    try:
        page.goto(f"{base}/modules/{slug}", wait_until="domcontentloaded")

        hero = page.get_by_test_id("landing-hero")
        assert hero.locator("h1").inner_text().strip(), "the page has a headline"
        assert hero.locator("p.home-lead").inner_text().strip(), "the hero says what it is"

        questions = page.get_by_test_id("module-question")
        assert questions.count() >= 3, "at least three example questions"
        for i in range(questions.count()):
            assert questions.nth(i).locator("h3").inner_text().strip().endswith("?")
            assert re.match(r"Asked by \S", questions.nth(i).get_by_test_id("module-question-who").inner_text())

        steps_heading = page.get_by_test_id("module-steps").locator("h2").inner_text().strip()
        waiting = (page.get_by_test_id("module-cta").get_attribute("href") or "").startswith("#")
        if waiting:
            assert "data to an answer" not in steps_heading, f"a waiting-list page's steps heading fits joining: {steps_heading!r}"
        else:
            assert steps_heading == "From your data to an answer, in steps."

        steps = page.get_by_test_id("module-step")
        assert steps.count() >= 3, "the steps of the job"
        for i in range(steps.count()):
            assert steps.nth(i).locator(".public-stepper__body").inner_text().strip()

        media = page.get_by_test_id("module-media")
        if slug in CAPTURED:
            figure = media.locator("[data-testid='page-screenshot'] img, [data-testid='page-recording'] video")
            assert figure.count() >= 1, "the page shows the product (recording or screen)"
            alt = figure.first.get_attribute("alt") or figure.first.get_attribute("aria-label") or ""
            assert len(alt.split()) >= 6, f"the figure's alt text says what it shows, got {alt!r}"
        else:
            assert media.count() == 0, "a page with no real capture shows no empty frame"

        text = page.locator("main, body").first.inner_text()
        assert not DATES.search(text), f"no quarter, wave or year date: {DATES.search(text).group(0) if DATES.search(text) else ''}"

        cta = page.get_by_test_id("module-cta")
        href = cta.get_attribute("href")
        if href.startswith("#"):
            assert page.locator(href).count() == 1, "the call to action lands on the form it names"
        else:
            cta.click()
            page.wait_for_load_state("domcontentloaded")
            assert "/register" in page.url or "/account/" in page.url
            assert page.locator("form input[type=email], form input[name=email]").count() >= 1, "sign-up has its form"
    finally:
        page.close()


@pytest.mark.parametrize("slug", [s for s in SLUGS if s not in MERGED])
def test_module_page_does_not_scroll_sideways_on_a_phone(browser, live_server, slug):
    context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    page = context.new_page()
    try:
        page.goto(f"{str(live_server).rstrip('/')}/modules/{slug}", wait_until="load")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"{slug} scrolls sideways by {overflow}px at 390"
        if slug in PROOF_PAGES:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS / f"{slug}-390-dark.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("slug", PROOF_PAGES)
def test_proof_pages_at_desktop_light(visitor, slug):
    context, base = visitor
    page = context.new_page()
    try:
        page.goto(f"{base}/modules/{slug}", wait_until="load")
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{slug}-1440-light.png"), full_page=True)
    finally:
        page.close()
