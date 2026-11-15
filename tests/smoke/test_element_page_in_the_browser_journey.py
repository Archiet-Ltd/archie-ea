"""Journey, in a browser: a CTO opens an element and learns what it is and what depends on it.

The demo company (Lantern Quay Systems) is seeded the way the product's own seed does it, and the demo architect
signs in through the login form. For each element type that matters (a stage, an application, a capability, a
node, a project, a goal) the page is opened at desktop (1440x900) and phone (390x844) width and judged as a person
would judge it:

* no program text on the page (a raw object, a Python list or dict, "None", "nan", "undefined");
* the depends-on and used-by figures are on the first screen, and together they equal the direct count on the
  Impact analysis page, which is reached by clicking the Impact analysis button;
* the first connection is a link, and it opens a page headed with that element's name;
* on a phone nothing scrolls sideways, the actions are reachable without a tab bar, and the loud action comes
  before Delete;
* dark mode applies and raises no page error.
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
TYPES = ["Plateau", "ApplicationComponent", "Capability", "Node", "WorkPackage", "Goal"]
SIZES = [("desktop", 1440, 900), ("phone", 390, 844)]
RAW_TEXT = [r"\[<", r"order=", r"\{'", r"\bNone\b", r"\bnan\b", r"\bundefined\b", r"\[object Object\]"]


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demo company; return {type: (layer, id, name)} for the most connected element of each type."""
    from sqlalchemy import text

    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            picks = {}
            for kind in TYPES:
                row = db.session.execute(text(
                    "select e.layer, e.id, e.name from archimate_elements e "
                    "join organizations o on o.id = e.organization_id "
                    "where o.slug = 'lantern-quay' and e.type = :t "
                    "order by (select count(*) from archimate_relationships r "
                    "         where r.source_id = e.id or r.target_id = e.id) desc, e.id limit 1"), {"t": kind}).first()
                assert row, "the demo company has no %s" % kind
                picks[kind] = (row[0], row[1], row[2])
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return picks


def _sign_in(page, base):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", DEMO_EMAIL)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, "could not sign in as the demo architect"


def _open(page, base, picks, kind):
    layer, element_id, name = picks[kind]
    page.goto("%s/architecture/%s/%s/%d" % (base, layer, kind, element_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(500)
    return name


def _figure(page, testid):
    card = page.locator("[data-testid='%s']" % testid)
    assert card.count() == 1 and card.is_visible(), "no visible %s figure on the first screen" % testid
    return int(card.locator(".tabular-nums").inner_text().strip())


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize("size", SIZES, ids=[s[0] for s in SIZES])
def test_element_page_answers_what_depends_on_it(browser, live_server, demo, kind, size):
    _, width, height = size
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))
    try:
        _sign_in(page, live_server)
        name = _open(page, live_server, demo, kind)
        detail_url = page.url

        # no program text anywhere on the page
        text = re.sub(r"\s+", " ", page.inner_text("main"))
        for pattern in RAW_TEXT:
            found = re.search(r".{0,40}%s.{0,40}" % pattern, text)
            assert not found, "%s page shows raw text %r: %s" % (kind, pattern, found.group(0) if found else "")
        assert page.locator("h1").all_inner_texts() == [name]

        # the figures are on the first screen and equal the Impact analysis page's direct count
        depends_on, used_by = _figure(page, "kpi-depends-on"), _figure(page, "kpi-used-by")
        page.locator("[data-testid='impact-analysis-link']").click()
        page.wait_for_url(lambda url: "/architecture/impact/" in url, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        impact_text = re.sub(r"\s+", " ", page.inner_text("main"))
        direct = re.search(r"(\d+) Direct \(1 hop\)", impact_text)
        assert direct, "the Impact analysis page shows no direct count: %s" % impact_text[:200]
        assert depends_on + used_by == int(direct.group(1)), (kind, depends_on, used_by, direct.group(1))
        page.goto(detail_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # the first connection opens the element it names
        link = page.locator("[data-testid='connection-row'] a").first
        assert link.count() == 1, "%s lists no connection to open" % kind
        target = link.inner_text().strip()
        link.click()
        page.wait_for_url(lambda url: url != detail_url, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("h1").all_inner_texts() == [target], (kind, target, page.locator("h1").all_inner_texts())
        assert len(page.inner_text("main")) > 200, "the page behind %r is empty" % target
        page.goto(detail_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # the actions are all there, the loud one first, Delete after it, and no tab bar to find them in
        assert page.locator("[role='tablist']").count() == 0
        for testid in ("impact-analysis-link", "delete-element"):
            assert page.locator("[data-testid='%s']" % testid).is_visible(), testid
        assert page.evaluate("""() => !!(document.querySelector('[data-testid=impact-analysis-link]')
            .compareDocumentPosition(document.querySelector('[data-testid=delete-element]')) & Node.DOCUMENT_POSITION_FOLLOWING)"""), \
            "Delete comes before Impact analysis"
        if width < 500:
            assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1"), \
                "%s page scrolls sideways on a phone" % kind
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("kind", TYPES)
def test_element_page_in_dark_mode_applies_the_theme_without_errors(browser, live_server, demo, kind):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("try { localStorage.setItem('theme', 'dark') } catch (e) {}")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))
    try:
        _sign_in(page, live_server)
        _open(page, live_server, demo, kind)
        assert page.evaluate("() => document.documentElement.classList.contains('dark')"), "dark class not applied"
        assert page.locator("[data-testid='kpi-band']").is_visible()
        assert not errors, errors
    finally:
        context.close()
