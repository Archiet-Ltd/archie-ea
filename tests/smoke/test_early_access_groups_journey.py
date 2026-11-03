"""A buyer, signed out, follows every "join the waiting list" link on the features, about and pricing pages and
reads the early-access hub. An early-access page exists only for what is not built: each one is for its own
capability and joins its own waiting list, and a capability that works today never offers one, its early-access
address leading on to where it is sold."""

from __future__ import annotations

import re

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = {"1440x900": (1440, 900), "390x844": (390, 844)}
PAGES = ("/features", "/about", "/pricing")


def _groups():
    from app.release_waves import code_slug, wave_map

    return [(code, info, code_slug(code)) for code, info in wave_map()["groups"].items() if info.get("page", True) is not False]


def _h1(page) -> str:
    return re.sub(r"\s+", " ", page.locator("h1").first.inner_text()).strip()


@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_each_waiting_list_link_is_for_its_own_unbuilt_capability(browser, live_server, viewport_name):
    width, height = VIEWPORTS[viewport_name]
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    live = {info["title"]: info["live"] for _c, info, _s in _groups() if info.get("live")}
    try:
        lines: list[tuple[str, str, str]] = []
        for path in PAGES:
            assert page.goto(f"{live_server}{path}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT).status == 200
            for row in page.locator("main p, main li").evaluate_all(
                "els => els.filter(e => /^early access/i.test(e.textContent.trim()) && e.querySelector('a'))"
                ".map(e => [e.textContent.trim(), e.querySelector('a').getAttribute('href')])"
            ):
                lines.append((path, row[0], row[1]))
        features = [line for line in lines if line[0] == "/features"]
        assert len(features) >= 12, f"only {len(features)} early-access lines with a link on /features"

        targets: dict[str, str] = {}
        for path, text, href in lines:
            # a link to the hub is a picker, not the waiting list of this capability
            assert href.rstrip("/") != "/early-access", f"{path}: {text[:60]!r} links to the hub, not its own waiting list"
            reply = page.request.get(f"{live_server}{href}", max_redirects=0, timeout=PAGE_TIMEOUT)
            assert reply.status == 200, f"{path}: {href} answered {reply.status}, so it is not a waiting list of its own"
            page.goto(f"{live_server}{href}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            heading = _h1(page)
            body = page.locator("main").first.inner_text()
            assert heading and len(body.split()) > 40 and "waiting list" in body.lower(), f"{href} has no content"
            assert heading not in live, f"{path}: {href} offers a waiting list for {heading!r}, which works today"
            if path == "/features":
                assert targets.setdefault(href, text) == text, f"two early-access lines share {href}"

        # The hub lists only what is not built, and each entry opens its own waiting list.
        page.goto(f"{live_server}/early-access", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        hub = page.locator('[data-testid="early-access-hub"] a[href^="/early-access/"]').evaluate_all(
            "els => els.map(a => [a.textContent.trim(), a.getAttribute('href')])"
        )
        assert len(hub) >= 10, hub
        for title, href in hub:
            assert title not in live, f"the hub lists {title!r}, which works today"
            reply = page.request.get(f"{live_server}{href}", max_redirects=0, timeout=PAGE_TIMEOUT)
            assert reply.status == 200, f"hub entry {title!r} answered {reply.status}"

        # A capability that works today has no waiting list: its early-access address moves on to where it is sold.
        assert live, "no capability is marked as working today"
        for _code, info, slug in (g for g in _groups() if g[1].get("live")):
            reply = page.request.get(f"{live_server}/early-access/{slug}", max_redirects=0, timeout=PAGE_TIMEOUT)
            assert reply.status == 301, f"/early-access/{slug} answered {reply.status}"
            assert reply.headers["location"].endswith(info["live"]), (slug, reply.headers["location"])
            landing = page.goto(f"{live_server}/early-access/{slug}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert landing.status == 200 and len(page.locator("main").first.inner_text().split()) > 40, slug
            assert "waiting list" not in page.locator("main").first.inner_text().lower().split("early access")[0]

        # Joining a waiting list from a features-page link thanks the person.
        page.goto(f"{live_server}{features[0][2]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#early-access-email", f"buyer-{viewport_name}@example.com")
        page.check("#early-access-consent")
        page.click('[data-testid="early-access-submit"]')
        page.wait_for_selector('[data-testid="early-access-thanks"]', timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()
