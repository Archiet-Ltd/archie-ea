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
    ("/auto-dashboard/", "Systems"),
    ("/strategic/capability-health", "Impact"),
    ("/enterprise/", "Changes"),
    ("/enterprise/strategic/capability-health", "Changes"),
    ("/enterprise/strategic/risk-assessment", "Changes"),
    ("/enterprise/analysis/process-optimization", "Changes"),
    ("/capability-map/", "Strategy"),
    ("/solutions/programmes", "Changes"),
    ("/architecture/decisions/", "Compliance"),
]

CRUMBS = "nav[aria-label*=readcrumb i], [data-slot=breadcrumb]"


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
    texts = [" > ".join(line.strip() for line in t.splitlines() if line.strip()) for t in navs.all_inner_texts()]
    return texts


def _check(page, base, path, group, width, theme):
    _open(page, base, path)
    trails = _trail(page)
    assert len(trails) == 1, "%s shows %d breadcrumb trails: %s" % (path, len(trails), trails)
    crumb = trails[0]
    assert crumb.lower().startswith("home"), "%s trail does not start at Home: %r" % (path, crumb)
    home = page.locator(CRUMBS).locator("a").first
    assert home.get_attribute("href") == "/", "%s Home crumb goes to %r" % (path, home.get_attribute("href"))
    names = [c.lower() for c in crumb.split(" > ")[1:-1]]
    assert group.lower() in names, "%s trail %r does not name the group %r" % (path, crumb, group)
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


def _seed_risk(org_id):
    from app import create_app, db
    from app.models.risk import Risk, RiskStatus

    app = create_app("testing")
    with app.app_context():
        row = Risk(title="Breadcrumb ledger outage", likelihood=3, impact=4, status=RiskStatus.OPEN,
                   organization_id=org_id, owner="Risk Owner")
        db.session.add(row)
        db.session.commit()
        risk_id = row.id
        db.session.remove()
    return risk_id


@pytest.mark.parametrize("width,height,scheme", [(1440, 1000, "light"), (390, 900, "dark")])
def test_risk_register_and_risk_page_each_show_one_trail_under_compliance(
        live_server, browser, width, height, scheme):
    from .fresh_org import create_fresh_org, sign_in

    org = create_fresh_org("enterprise_architect")
    risk_id = _seed_risk(org["org_id"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height},
                                  color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _check(page, live_server, "/risks/", "Compliance", width, scheme)
        assert "Breadcrumb ledger outage" in page.locator("body").inner_text()
        _check(page, live_server, "/risks/%d" % risk_id, "Compliance", width, scheme)
        trail = _trail(page)[0]
        assert trail.endswith("Breadcrumb ledger outage"), trail
        assert "Governance" not in trail
    finally:
        context.close()
