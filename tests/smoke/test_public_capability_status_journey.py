"""A buyer, signed out, reads the home page, features, about and pricing pages to learn what Entelim
can do for them today and what is still to come. Every capability says plainly which it is: no
quarter, year or wave anywhere, every early-access item leads to a waiting list that answers, the
capabilities that work today carry no early-access mark, and no capability is both.

The persona is the visitor, so the journey asserts no one is signed in. The server runs with RELEASE_WAVE=1, the way
production runs the first release."""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = {"1440x900": (1440, 900), "390x844": (390, 844)}
PAGES = ("/", "/features", "/about", "/pricing")

# A quarter, a year from 2027 on, or a release plan phrase, beside a capability.
DATED = re.compile(
    r"\bQ[1-4]\b|\b20(?:2[7-9]|[3-9]\d)\b|\blater in 20\d\d\b|\bcoming in\b"
    r"|\b(?:first|next|later|second|third) waves?\b|\bwave [0-9]\b|\b(?:lands|arrives?|opens?) (?:in|from) (?:Q|20)",
)

# What works today, and where the page says so.
LIVE_ON_FEATURES = (
    "audit log",
    "owners of the applications it reaches",
    "connect the AI assistant your team already uses",
    "ServiceNow",
)
LIVE_ON_ABOUT = ("Model Context Protocol",)

# Built but not open at the first release (their main pages open in a later wave of wave_map.json): the features page
# says them only as early access, and no plan names them; the pricing page says them outside the plans.
EARLY_AT_FIRST_RELEASE = ("business case", "single sign-on", "architecture review board", "risk register")
NOT_IN_A_PLAN = ("single sign-on", "saml", "composer", "review-board", "review board")

# Not built yet: said only as early access, never as something to use today.
EARLY = (
    "control library",
    "renewal alerts",
    "AI system register",
    "scenario branches",
    "multi-step agent workflows",
    "developer kits",
    "skills and capacity planning",
    "key risk indicators",
    "data quality rules",
    "forecasts and recommendations",
)


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


def _shot_dir() -> pathlib.Path:
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "public-capability-status"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paragraphs(page) -> list[str]:
    return [t.strip() for t in page.locator("main p, main li").all_inner_texts() if t.strip()]


def _is_early(text: str) -> bool:
    return text.lower().startswith("early access")


@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_buyer_learns_what_works_today_and_what_is_early_access(browser, wave_server, viewport_name):
    live_server = wave_server
    width, height = VIEWPORTS[viewport_name]
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    shots = _shot_dir()
    texts: dict[str, str] = {}
    paras: dict[str, list[str]] = {}
    waiting_links: set[str] = set()
    try:
        for path in PAGES:
            response = page.goto(f"{live_server}{path}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200, f"{path} answered {response.status}"
            assert not [c for c in ctx.cookies() if c["name"] == "remember_token"]
            texts[path] = page.locator("main").first.inner_text()
            paras[path] = _paragraphs(page)
            page.screenshot(path=str(shots / f"{path.strip('/') or 'home'}-{viewport_name}.png"), full_page=True)

            # No quarter, year or wave beside any capability.
            dated = [m.group(0) for m in DATED.finditer(texts[path])]
            assert not dated, f"{path} dates a capability: {dated}"

            # Every early-access item leads to a waiting list that answers with content.
            for text in (p for p in paras[path] if _is_early(p)):
                assert "waiting list" in text.lower(), f"{path}: early-access line without a waiting list: {text[:80]!r}"
            for href in page.locator("main a[href]").evaluate_all(
                "els => els.filter(a => /waiting list/i.test(a.textContent)).map(a => a.getAttribute('href'))"
            ):
                waiting_links.add(href)

        features, about, pricing = paras["/features"], paras["/about"], paras["/pricing"]

        # Early access on the features page is real: many lines, each with its own waiting-list link.
        early_lines = [p for p in features if _is_early(p)]
        assert len(early_lines) >= 12, f"only {len(early_lines)} early-access lines on /features"
        assert "/early-access" in " ".join(waiting_links) and "/modules/scenario-modelling" in waiting_links

        # Each waiting-list link lands on a page that answers and says what the person can join.
        for href in sorted(waiting_links):
            landing = page.goto(f"{live_server}{href}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert landing.status == 200, f"{href} answered {landing.status}"
            body = page.locator("main").first.inner_text()
            assert len(body.split()) > 40, f"{href} has no content"
            assert "waiting list" in body.lower(), f"{href} does not offer the waiting list"
            assert page.locator("h1").first.inner_text().strip(), f"{href} has no heading"

        # What works today appears without an early-access mark.
        for phrase in LIVE_ON_FEATURES:
            holders = [p for p in features if phrase.lower() in p.lower()]
            assert holders, f"/features does not mention {phrase!r}"
            assert not any(_is_early(p) for p in holders), f"{phrase!r} is marked early access on /features"
        for phrase in LIVE_ON_ABOUT:
            holders = [p for p in about if phrase.lower() in p.lower()]
            assert holders and not any(_is_early(p) for p in holders)
        assert any("mcp" in h.lower() or "model context protocol" in h.lower() for h in
                   [p for p in about if "Model Context Protocol" in p]), "about does not say the protocol is live"

        # No capability is both live and early access: each early one is only ever said as early access.
        for phrase in EARLY:
            holders = [p for p in features if phrase.lower() in p.lower()]
            assert holders and all(_is_early(p) for p in holders), (
                f"{phrase!r} is described as usable today: {[p[:80] for p in holders if not _is_early(p)]}"
            )

        for phrase in EARLY_AT_FIRST_RELEASE:
            holders = [p for p in features if phrase.lower() in p.lower()]
            assert holders and all(_is_early(p) for p in holders), (
                f"{phrase!r} is described as usable today: {[p[:80] for p in holders if not _is_early(p)]}"
            )

        # The plans list only what works today: no early-access capability sits in a plan card or the table.
        page.goto(f"{live_server}/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        plans = page.locator('[data-testid="pricing-plans"], [data-testid="pricing-compare"]').all_inner_texts()
        plan_text = " ".join(plans).lower()
        assert plan_text.strip()
        for phrase in EARLY:
            assert phrase.lower() not in plan_text, f"a plan lists {phrase!r}, which is early access"
        for phrase in NOT_IN_A_PLAN:
            assert phrase not in plan_text, f"a plan card or the comparison lists {phrase!r}, whose page is closed"
        assert "early access" not in plan_text, "a plan card or the comparison carries an early-access line"
        # Early access is said beside the plans, each line with a waiting list that lands on a page with content.
        early = page.locator('[data-testid="pricing-early-access"]')
        assert early.count() == 1, "the pricing page does not say what is in early access"
        early_text = early.inner_text().lower()
        for phrase in ("composer", "single sign-on", "review board"):
            assert phrase in early_text, f"the early-access list on /pricing does not name {phrase!r}"
        # Nothing in it is included yet, so no line carries the included tick the plan cards use.
        ticked = early.locator("li").evaluate_all(
            "els => els.filter(li => getComputedStyle(li, '::before').display !== 'none').map(li => li.textContent.trim())"
        )
        assert not ticked, f"an early-access line on /pricing carries the included tick: {ticked}"
        early_hrefs = early.locator("a[href]").evaluate_all("els => els.map(a => a.getAttribute('href'))")
        assert early_hrefs and all(h.startswith("/early-access") for h in early_hrefs), early_hrefs
        for href in sorted(set(early_hrefs)):
            landing = page.goto(f"{live_server}{href}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert landing.status == 200, f"{href} answered {landing.status}"
            body = page.locator("main").first.inner_text()
            assert len(body.split()) > 40 and "waiting list" in body.lower(), f"{href} has no waiting list"
        page.goto(f"{live_server}/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # The plan-gains promise names no date and says early-access capabilities reach Startup and Team.
        promise = [p for p in pricing if "early access" in p.lower()]
        assert promise and "Startup and Team" in promise[0] and "no extra charge" in promise[0]
    finally:
        ctx.close()
