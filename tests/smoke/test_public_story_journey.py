"""A startup CTO, signed out, follows the public story from the features page to a first
answer: connect GitHub, approve what it found, ask in the AI chat. Every link the reader
clicks lands on a page with content, at both required widths.

The persona is the visitor, so the journey asserts no one is signed in."""

from __future__ import annotations

import os
import pathlib

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = {"1440x900": (1440, 900), "390x844": (390, 844)}


def _shot_dir() -> pathlib.Path:
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "public-story"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _body_words(page) -> int:
    return len(page.inner_text("main, article, body").split())


def _click_and_land(page, locator, url_part: str, heading_part: str):
    locator.click()
    page.wait_for_url(lambda url: url_part in url, timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")
    heading = page.locator("h1").first.inner_text()
    assert heading_part.lower() in heading.lower(), f"landed on {page.url} with heading {heading!r}"
    assert _body_words(page) > 100, f"{page.url} has no content"


@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_startup_cto_follows_the_story_from_features_to_the_first_answer(browser, live_server, viewport_name):
    width, height = VIEWPORTS[viewport_name]
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    shots = _shot_dir()
    try:
        # The persona is a signed-out visitor: no session, and the page offers sign-in.
        page.goto(f"{live_server}/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert not [c for c in ctx.cookies() if c["name"] in ("session", "remember_token")]
        assert page.locator('a[href*="login"], a[href*="sign-in"], a[href*="signin"]').count() >= 1

        # Features opens on the model built from your tools and the cited answers in the chat.
        opening = page.locator("article").first.inner_text()[:1200]
        assert "GitHub" in opening and "AI chat" in opening
        assert "Ask page" not in page.locator("article").first.inner_text()
        page.screenshot(path=str(shots / f"features-{viewport_name}.png"), full_page=True)

        # The Integration line points at Connectors; the link lands on the GitHub-first page.
        connectors = page.locator('#integration-and-interoperability ~ p a[href="/connectors"]').first
        _click_and_land(page, connectors, "/connectors", "Connect GitHub")
        text = page.locator("article").first.inner_text()
        assert text.index("GitHub") < 300
        for step in ("Sign up free", "Connect GitHub", "Approve all", "Ask in the AI chat"):
            assert step in text, f"/connectors does not state the step {step!r}"
        page.screenshot(path=str(shots / f"connectors-{viewport_name}.png"), full_page=True)

        # From there, the page about not typing every system in starts with GitHub.
        _click_and_land(
            page,
            page.locator('article a[href="/use-cases/import-from-jira-and-github"]').first,
            "/use-cases/import-from-jira-and-github",
            "Connect GitHub and ask what breaks",
        )
        body = page.locator("article").first.inner_text()
        assert "How to build the model from GitHub" in body
        assert body.index("GitHub") < body.index("ServiceNow")
        page.screenshot(path=str(shots / f"import-from-github-{viewport_name}.png"), full_page=True)

        # The use-case hub lists that page first under Startups.
        page.goto(f"{live_server}/use-cases", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        first_startup = page.locator("article h2:has-text('Startups') + ul li a").first
        assert first_startup.inner_text().startswith("Connect GitHub and ask what breaks")
        page.screenshot(path=str(shots / f"use-cases-{viewport_name}.png"), full_page=True)

        # The roles hub opens with the startup and scale-up CTO, whose page has content.
        page.goto(f"{live_server}/roles", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("article h1").first.inner_text().startswith("Startup and scale-up CTOs")
        page.screenshot(path=str(shots / f"roles-{viewport_name}.png"), full_page=True)
        _click_and_land(
            page,
            page.locator('article a[href="/roles/scale-up-cto-and-vp-engineering"]').first,
            "/roles/scale-up-cto-and-vp-engineering",
            "Know what you run",
        )

        # A question page asks in the chat and its links land on content.
        page.goto(
            f"{live_server}/use-cases/what-breaks-and-who-gets-called",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        question = page.locator("article").first.inner_text()
        assert "AI chat" in question and "Ask page" not in question and "On Ask" not in question
        page.screenshot(path=str(shots / f"what-breaks-{viewport_name}.png"), full_page=True)
        _click_and_land(
            page,
            page.locator('article a[href="/modules/digital-twin"]').first,
            "/modules/digital-twin",
            "",
        )

        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        assert scroll_width <= page.evaluate("() => window.innerWidth"), f"horizontal scroll at {viewport_name}"
    finally:
        ctx.close()
