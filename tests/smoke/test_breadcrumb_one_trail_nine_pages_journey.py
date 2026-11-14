"""Nine pages that once broke the breadcrumb rule each show exactly one trail, under the sidebar's group.

A person opening any signed-in page sees one breadcrumb trail, starting at Home, and the group
in that trail is the group of the sidebar that opens the page. Three pages named a different
group from the sidebar, six showed no trail at all, and two Home links went somewhere else.

Screenshots go to BCRUMB_WALK_DIR when it is set.
"""
import os

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("BCRUMB_WALK_DIR")

# path -> the sidebar group (or, for a page the sidebar does not list, its nearest listed parent)
PAGES = [
    ("/applications/model-dashboard/ApplicationComponent", None),
    ("/strategic/capability-health", None),
    ("/enterprise/", None),
    ("/enterprise/strategic/capability-health", None),
    ("/enterprise/strategic/risk-assessment", None),
    ("/enterprise/analysis/process-optimization", None),
    ("/capability-map/", "Strategy"),
    ("/solutions/programmes", "Changes"),
    ("/architecture/decisions/", "Compliance"),
]

CRUMBS = "nav[aria-label*=readcrumb i], [data-slot=breadcrumb]"

# the sidebar entries that hold a link marked as the current page: the expandable entry above a
# sub-link (Systems above Applications), and the section heading above that
ACTIVE_GROUP_JS = """() => [...document.querySelectorAll('[data-slot=sidebar] a[aria-current=page], aside a[aria-current=page]')]
  .flatMap(a => {
    const item = a.closest('[data-slot=sidebar-menu-item]');
    const parent = item && item.querySelector('[data-slot=sidebar-menu-button]');
    const g = a.closest('[data-slot=sidebar-group]');
    const l = g && g.querySelector('[data-slot=sidebar-group-label]');
    return [parent && parent !== a ? parent.innerText.trim() : null, l ? l.innerText.trim() : null];
  })
  .filter(Boolean)"""


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", "SmokeJourney!2026")
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, "%s answered %s" % (path, response.status)
    page.wait_for_timeout(500)


def _trail(page):
    navs = page.locator(CRUMBS)
    texts = [" > ".join(t.split()) for t in navs.all_inner_texts()]
    return texts


def _check(page, base, path, group, width, theme):
    _open(page, base, path)
    trails = _trail(page)
    assert len(trails) == 1, "%s shows %d breadcrumb trails: %s" % (path, len(trails), trails)
    crumb = trails[0]
    assert crumb.lower().startswith("home"), "%s trail does not start at Home: %r" % (path, crumb)
    home = page.locator(CRUMBS).locator("a").first
    assert home.get_attribute("href") == "/", "%s Home crumb goes to %r" % (path, home.get_attribute("href"))
    expected = [group] if group else page.evaluate(ACTIVE_GROUP_JS)
    if expected:
        names = [c.lower() for c in crumb.split(" > ")[1:-1]]
        assert any(e.lower() in names for e in expected), (
            "%s trail %r names none of the sidebar groups %r" % (path, crumb, expected))
    assert len(crumb.split(" > ")) >= 2, "%s trail %r names no page" % (path, crumb)
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        slug = path.strip("/").replace("/", "_")
        page.screenshot(path=os.path.join(WALK_DIR, "%s-%d-%s.png" % (slug, width, theme)), full_page=True)


@pytest.mark.parametrize("path,group", PAGES, ids=[p for p, _ in PAGES])
def test_one_trail_naming_the_sidebar_group_at_1440_light(browser, live_server, seeded, path, group):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000},
                                  color_scheme="light")
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        _check(page, live_server, path, group, 1440, "light")
    finally:
        context.close()


@pytest.mark.parametrize("path,group", [PAGES[0], PAGES[6], PAGES[7]], ids=[PAGES[0][0], PAGES[6][0], PAGES[7][0]])
def test_one_trail_naming_the_sidebar_group_at_390_dark(browser, live_server, seeded, path, group):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 900},
                                  color_scheme="dark")
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        _check(page, live_server, path, group, 390, "dark")
    finally:
        context.close()
