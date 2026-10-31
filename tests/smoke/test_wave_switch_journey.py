"""Journey: with RELEASE_WAVE=1 a visitor and a signed-in CTO see only what the first release opens.

Written from the goal "the first release shows only what works and says plainly what is coming":

* a visitor opens the home page and clicks every link in the navbar and the footer: each lands on a page
  with content, and only the labelled Coming soon group leads to early-access pages;
* the sitemap lists only addresses that answer with their own content;
* a visitor opening a later release's module address reads its early-access page and joins the waiting list,
  and the enquiry is stored;
* a signed-in CTO's sidebar offers only first-release pages, each opening with content, and none of the
  later waves' entries;
* a signed-in administrator's sidebar offers the AI chat and the connectors, and /admin/connectors/github and
  /ai-chat open as pages, not as the early-access page;
* a write sent to a later release's route answers 404 and stores nothing.

The same journey run with the switch off (WAVE_JOURNEY_SWITCH=off) is red: that is the "before".
"""

from __future__ import annotations

import os
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org, boot_live_server
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SWITCH = "" if os.environ.get("WAVE_JOURNEY_SWITCH") == "off" else "1"
LATER_MODULE = "/modules/diagrams"          # its screen opens in wave 3
LATER_WRITE = "/archimate/api/create-diagram-from-elements"   # a later wave's route that takes writes


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": SWITCH})


@pytest.fixture(scope="module")
def org(request):
    return _seed_standard_org(request, None)


def _hrefs(page, selector):
    found = page.eval_on_selector_all(
        selector, "els => els.map(e => [e.getAttribute('href'), (e.innerText || '').trim()])")
    seen, out = set(), []
    for href, label in found:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")) or href in seen:
            continue
        if href.startswith("http") and "127.0.0.1" not in href:
            continue
        path = href.split("#")[0]
        if not path or path in seen:
            continue
        seen.add(path)
        out.append((path, label))
    return out


def _lands_with_content(page, base, href):
    """Open one link; return (status, is_early_access, heading)."""
    url = href if href.startswith("http") else base + href
    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status = response.status if response else 0
    early = page.locator('[data-testid="early-access-page"]').count() > 0
    headings = [h.strip() for h in page.locator("h1, h2, [data-slot='card-title']").all_inner_texts() if h.strip()]
    return status, early, (headings[0] if headings else "")


def test_visitor_navbar_footer_and_sitemap_lead_only_to_pages_with_content(browser, wave_server, org):
    page = browser.new_page()
    try:
        page.goto(wave_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        nav = _hrefs(page, "header[data-site-nav] a[href]")
        foot = _hrefs(page, "footer[data-site-footer] a[href]")
        soon_nav = {h for h, _ in _hrefs(page, '[data-menu-group="coming-soon"] a[href]')}
        soon_foot = {h for h, _ in _hrefs(page, '[data-footer-column="coming-soon"] a[href]')}
        assert len(nav) >= 8 and len(foot) >= 8, (nav, foot)
        coming = soon_nav | soon_foot
        problems = []
        for href, label in nav + foot:
            status, early, heading = _lands_with_content(page, wave_server, href)
            if status != 200 or not heading:
                problems.append("%s (%s) -> %s, heading %r" % (label, href, status, heading))
            elif len(page.inner_text("body").strip()) < 200:
                problems.append("%s (%s) opens a page with no content beyond its heading" % (label, href))
            elif early and href not in coming:
                problems.append("%s (%s) is listed as available but opens its early-access page" % (label, href))
        assert not problems, "\n".join(problems)

        xml = page.request.get(wave_server + "/sitemap.xml").text()
        paths = re.findall(r"<loc>https://entelim\.org([^<]*)</loc>", xml)
        assert paths
        for path in paths:
            status, early, heading = _lands_with_content(page, wave_server, path)
            assert status == 200 and heading and not early, "sitemap lists %s -> %s %r early=%s" % (
                path, status, heading, early)
    finally:
        page.close()


def test_visitor_reads_the_early_access_page_and_joins_the_waiting_list(browser, wave_server, org, app):
    page = browser.new_page()
    email = "wave-journey-%s@example.com" % os.getpid()
    try:
        status, early, heading = _lands_with_content(page, wave_server, LATER_MODULE)
        assert status == 200 and early, "a later release's module address must show its early-access page"
        assert heading
        body = page.inner_text("body")
        assert "Join the waiting list" in body and "not available yet" in body.lower()
        assert page.locator("img[src*='/static/img/modules/']").count() == 0, "no screenshot of a screen that is not open"
        page.fill("#early-access-email", email)
        page.check("#early-access-consent")
        page.click('[data-testid="early-access-submit"]')
        page.wait_for_selector('[data-testid="early-access-thanks"]', timeout=PAGE_TIMEOUT)
        with app.app_context():
            from app.models.product_inquiry import ProductInquiry

            rows = ProductInquiry.query.filter_by(email=email).all()
            assert len(rows) == 1 and rows[0].offer.startswith("early-access:")
            from app import db

            db.session.delete(rows[0])
            db.session.commit()
        # the hub of what is coming opens, and each link lands on content
        page.goto(wave_server + "/early-access", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        links = _hrefs(page, '[data-testid="early-access-hub"] a[href^="/early-access/"]')
        assert len(links) >= 10
        for href, label in links[:6]:
            status, early, heading = _lands_with_content(page, wave_server, href)
            assert status == 200 and early and heading, (href, status, heading)
    finally:
        page.close()


def test_cto_sidebar_offers_only_first_release_pages_and_each_opens_with_content(browser, wave_server, org):
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["cto"])
        page.goto(wave_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        side = _hrefs(page, '[data-slot="sidebar"] a[href]')
        assert len(side) >= 3, "the sidebar must offer the first release's pages: %s" % side
        offered = {h.split("?")[0].rstrip("/") for h, _ in side}
        for later in ("/capability-map", "/value-streams", "/architecture/business/BusinessActor", "/adm-kanban"):
            assert later not in offered, "%s opens in a later release but the sidebar offers it" % later
        problems = []
        for href, label in side:
            if href.startswith(("/account/logout",)):
                continue
            status, early, heading = _lands_with_content(page, wave_server, href)
            if status != 200 or early or not heading:
                problems.append("%s (%s) -> %s early=%s heading=%r" % (label, href, status, early, heading))
        assert not problems, "\n".join(problems)
        # a later release's address, typed by a signed-in person, says what it is and when to join
        status, early, heading = _lands_with_content(page, wave_server, "/capability-map")
        assert status == 200 and early and heading
    finally:
        page.close()


def test_administrator_sidebar_offers_ai_chat_and_connectors_and_both_open_as_pages(browser, wave_server, org):
    """The two things a first customer does after signing in: ask the AI chat, connect GitHub."""
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["platform_admin"])
        page.goto(wave_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        side = _hrefs(page, '[data-slot="sidebar"] a[href]')
        offered = [(h.split("?")[0].rstrip("/"), label.lower()) for h, label in side]
        assert any(h.startswith("/ai-chat") for h, _ in offered), "the sidebar must offer the AI chat: %s" % offered
        assert any(h.startswith("/admin/connectors") for h, _ in offered), "the sidebar must offer the connectors: %s" % offered
        for path in ("/admin/connectors/github", "/ai-chat"):
            status, early, heading = _lands_with_content(page, wave_server, path)
            assert status == 200 and heading, "%s -> %s %r" % (path, status, heading)
            assert not early, "%s opens as the early-access page for a signed-in administrator" % path
            assert len(page.inner_text("main")) > 80, "%s opens without content" % path
    finally:
        page.close()


def test_a_write_to_a_later_release_route_answers_404_and_stores_nothing(browser, wave_server, org, app):
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["cto"])
        with app.app_context():
            from app.models.archimate_core import SavedDiagram

            before = SavedDiagram.query.count()
        # the form token a signed-in page carries, as the browser would send it
        page.goto(wave_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        token = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
        response = page.request.post(wave_server + LATER_WRITE, headers={"X-CSRFToken": token}, data={"name": "x"})
        assert response.status == 404, response.status
        assert response.json()["error"] == "early_access"
        with app.app_context():
            assert SavedDiagram.query.count() == before
    finally:
        page.close()


def test_no_first_release_page_triggers_an_early_access_404(browser, wave_server, org):
    """The calls a wave-1 screen makes must be wave-1 routes too: open each first-release page signed in, press
    its controls, and fail on any request that answers with the early-access message. The routes found this way
    (notification bell, business-unit scope, workflow list, application diagram, element correction) are
    overrides in app/release_waves/build.py."""
    if SWITCH == "":
        pytest.skip("the switch is off, nothing answers early access")
    page = browser.new_page()
    refused = []

    def on_response(resp):
        if resp.status in (404, 405) and "/static/" not in resp.url:
            try:
                body = resp.text()
            except Exception:  # noqa: BLE001
                body = ""
            if "early_access" in body or "early-access-page" in body:
                refused.append("%s %s on %s" % (resp.request.method, resp.url.replace(wave_server, ""), page.url.replace(wave_server, "")))

    try:
        page.on("response", on_response)
        _login(page, wave_server, org["emails"]["cto"])
        pages = ["/dashboard/overview", "/applications/", "/applications/?owner=me", "/dashboard/health",
                 "/account/manage", "/account/manage/connected-assistants", "/admin/billing/", "/admin/team", "/batch-import/new"]
        page.goto(wave_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        detail = [h for h in page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
                  if h and re.match(r"^/applications/[0-9]+/?$", h)]
        assert detail, "the seeded organisation must show at least one application to open"
        pages += [detail[0], detail[0].rstrip("/") + "/edit"]
        for path in pages:
            page.goto(wave_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(2500)
            buttons = page.locator("main button")
            for i in range(min(buttons.count(), 12)):
                try:
                    if buttons.nth(i).is_visible():
                        buttons.nth(i).click(timeout=2500)
                        page.wait_for_timeout(400)
                except Exception:  # noqa: BLE001
                    pass
                page.goto(wave_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert not refused, "a first-release screen called a route that is not in the first release:\n" + "\n".join(sorted(set(refused)))
    finally:
        page.close()
