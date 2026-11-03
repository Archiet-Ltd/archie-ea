"""A buyer, signed out, follows every "join the waiting list" link on the features, about and pricing pages and
reads the early-access hub. An early-access page exists only for what is not built: each one is for its own
capability and joins its own waiting list, and a capability that works today never offers one, its early-access
address leading on to where it is sold.

The server runs with RELEASE_WAVE=1, the way production runs the first release, and which groups are live is read
straight from wave_map.json: a group is live when one of its route rules opens at wave 1."""

from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = {"1440x900": (1440, 900), "390x844": (390, 844)}
PAGES = ("/features", "/about", "/pricing")


WAVE = 1
MAP_FILE = pathlib.Path(__file__).resolve().parents[2] / "app" / "release_waves" / "wave_map.json"
# Coming at the first release, and each described on the features page: each needs its own waiting list there.
COMING_ON_FEATURES = ("PG-BC", "PG-VEN", "PG-PEOPLE", "PG-DIA", "PG-SSO", "PG-DATA")


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": str(WAVE)})


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _groups():
    """(code, group, slug, live) for every group with an early-access page, live read from the map's own waves."""
    doc = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    opened = {e[1] for e in doc["rules"].values() if re.fullmatch(r"W\d", e[0]) and int(e[0][1]) <= WAVE}
    return [(code, info, _slug(info["title"]), code in opened)
            for code, info in doc["groups"].items() if info.get("page", True) is not False]


def _shot_dir() -> pathlib.Path:
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "early-access-groups"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _h1(page) -> str:
    return re.sub(r"\s+", " ", page.locator("h1").first.inner_text()).strip()


@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_each_waiting_list_link_is_for_its_own_unbuilt_capability(browser, wave_server, viewport_name):
    live_server = wave_server
    width, height = VIEWPORTS[viewport_name]
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    live = {info["title"] for _c, info, _s, is_live in _groups() if is_live}
    by_code = {code: (info, slug) for code, info, slug, _l in _groups()}
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
        # what is coming at this release, and sold on the features page, has its own waiting list there
        offered = {href for _p, _t, href in features}
        for code in COMING_ON_FEATURES:
            info, slug = by_code[code]
            assert code not in {c for c, _i, _s, is_live in _groups() if is_live}, f"{code} opens at wave {WAVE}"
            assert f"/early-access/{slug}" in offered, f"/features sells {info['title']!r}, which is coming, with no waiting list"

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
            if path == "/features" and not targets:
                page.screenshot(path=str(_shot_dir() / f"waiting-list-{viewport_name}.png"), full_page=True)
            if path == "/features":
                assert targets.setdefault(href, text) == text, f"two early-access lines share {href}"

        # The hub lists only what is not built, and each entry opens its own waiting list.
        page.goto(f"{live_server}/early-access", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        hub = page.locator('[data-testid="early-access-hub"] a[href^="/early-access/"]').evaluate_all(
            "els => els.map(a => [a.textContent.trim(), a.getAttribute('href')])"
        )
        assert len(hub) >= 10, hub
        page.screenshot(path=str(_shot_dir() / f"hub-{viewport_name}.png"), full_page=True)
        for title, href in hub:
            assert title not in live, f"the hub lists {title!r}, which works today"
            reply = page.request.get(f"{live_server}{href}", max_redirects=0, timeout=PAGE_TIMEOUT)
            assert reply.status == 200, f"hub entry {title!r} answered {reply.status}"

        # A capability that works today has no waiting list: its early-access address moves on to where it is sold.
        assert live, "no capability opens at this release"
        live_first = next(g[1] for g in _groups() if g[3])
        for _code, info, slug, _l in (g for g in _groups() if g[3]):
            reply = page.request.get(f"{live_server}/early-access/{slug}", max_redirects=0, timeout=PAGE_TIMEOUT)
            assert reply.status == 301, f"/early-access/{slug} answered {reply.status} at wave {WAVE}"
            target = reply.headers["location"]
            assert target.split("#")[0].rstrip("/").endswith("/features"), (slug, target)
            landing = page.goto(f"{live_server}/early-access/{slug}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert landing.status == 200 and len(page.locator("main").first.inner_text().split()) > 40, slug
            assert "waiting list" not in page.locator("main").first.inner_text().lower().split("early access")[0]
            anchor = target.partition("#")[2]
            if anchor:
                # the section the address leads to is on the page, and describes what the person works with today
                section = page.locator(f"[id='{anchor}']")
                assert section.count() == 1, (slug, target)
            if info is live_first:
                page.screenshot(path=str(_shot_dir() / f"live-group-moves-on-{viewport_name}.png"), full_page=True)

        # Joining a waiting list from a features-page link thanks the person.
        page.goto(f"{live_server}{features[0][2]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#early-access-email", f"buyer-{viewport_name}@example.com")
        page.check("#early-access-consent")
        page.click('[data-testid="early-access-submit"]')
        page.wait_for_selector('[data-testid="early-access-thanks"]', timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()
