"""One breadcrumb per page: the page header's, which follows the sidebar (R34).

QA-134 and QA-122 found two breadcrumb rows that disagreed on `/capability-roadmap`
and on the vendors page: the template's own hand-written trail, and the page
header's own, built from the sidebar through `page_trail()`. A person reading
either one and clicking it landed somewhere different from the other.

This journey signs in as an architect in the demonstration organisation (real,
linked data: a transformation initiative, a data entity, a risk, applications and
vendors) and, at 1440 light and 390 dark, opens six of the pages Group A fixed
plus the vendors page the navigation-registry fix in `app/navigation/registry.py`
covers. On each it asserts:

- exactly one `nav[aria-label="Breadcrumb"]`, never two stacked on top of each
  other and never zero;
- the first crumb is Home;
- the second crumb is the sidebar section that actually lists the page;
- on a page that shows one record (the initiative, the data entity), the last
  crumb is that record's own name, not a generic label.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_USER = "demo@lantern-quay.example.com"

# (path, expected crumb list). The last page (vendors) is the R34 lookup fix;
# the rest are Group A pages whose own breadcrumb block was removed.
PAGES = [
    ("/capability-roadmap", ["Home", "Changes", "Enterprise Capability Roadmap"]),
    (None, ["Home", "Changes", "Calibration Modernisation"]),      # portfolio detail, id filled in below
    (None, ["Home", "Systems", "Customer Analytics Profile"]),      # data entity detail, id filled in below
    ("/risks/", ["Home", "Compliance", "Risks"]),
    ("/applications/rationalization", ["Home", "Systems", "Portfolio"]),
    ("/governance/dashboard", ["Home", "Governance"]),
    ("/applications/vendors", ["Home", "Spend", "Vendors"]),
]

# Which of the above are record pages: the last crumb must be the record's own
# name, so a reader of this trail could not mistake it for any other record.
RECORD_PAGE_NAMES = {"Calibration Modernisation", "Customer Analytics Profile"}


@pytest.fixture(scope="module")
def demo_architect(live_server):
    """The demonstration organisation (real seed, real linked data) and an
    architect account in it, plus the ids this journey needs: the transformation
    initiative and the data entity the seed's own linked data names."""
    from app import create_app, db

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.process_data import DataEntity
            from app.models.user import User
            from app.models.vendor.vendor_organization import EnterpriseInitiative

            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_USER).one().organization_id
            entity = DataEntity.query.filter_by(
                organization_id=org_id, name="Customer Analytics Profile",
            ).one()
            initiative = EnterpriseInitiative.query.filter_by(
                organization_id=org_id, name="Calibration Modernisation",
            ).one()
            paths = []
            for path, crumbs in PAGES:
                if path is not None:
                    paths.append((path, crumbs))
                elif crumbs[-1] == initiative.name:
                    paths.append((f"/portfolio/{initiative.id}", crumbs))
                elif crumbs[-1] == entity.name:
                    paths.append((f"/architecture/data-entities/{entity.id}", crumbs))
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return {"email": DEMO_USER, "pages": paths}


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PASSWORD)
    try:
        page.click("button[type='submit']", no_wait_after=True)
    except TypeError:                      # newer Playwright dropped the kwarg
        page.locator("button[type='submit']").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _crumb_labels(page):
    """The text of every crumb in the one breadcrumb nav, in order."""
    items = page.locator('nav[aria-label="Breadcrumb"] li, nav[aria-label="breadcrumb"] li')
    labels = []
    for i in range(items.count()):
        text = items.nth(i).inner_text().strip()
        if text:
            labels.append(text)
    return labels


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_one_breadcrumb_following_the_sidebar(browser, live_server, demo_architect, width, dark):
    """Every page shows exactly one breadcrumb, and it reads the way the sidebar
    is laid out: Home, the section a person would actually click to reach the
    page, then the page or the record it shows."""
    ctx = browser.new_context(
        viewport={"width": width, "height": 900},
        color_scheme="dark" if dark else "light",
    )
    ctx.add_init_script("localStorage.setItem('theme', '%s');" % ("dark" if dark else "light"))
    page = ctx.new_page()
    try:
        _login(page, live_server, demo_architect["email"])
        for path, expected_crumbs in demo_architect["pages"]:
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status < 400, "%s returned %s" % (path, response.status)

            breadcrumb_nav = page.locator('nav[aria-label="Breadcrumb"], nav[aria-label="breadcrumb"]')
            expect(breadcrumb_nav).to_have_count(1)

            labels = _crumb_labels(page)
            assert labels, "%s rendered a breadcrumb nav with no crumbs" % path
            assert labels[0] == "Home", "%s: first crumb was %r, not Home" % (path, labels[0])
            assert len(labels) >= 2, "%s: no second crumb (the sidebar section)" % path
            assert labels[1] == expected_crumbs[1], (
                "%s: second crumb was %r, the sidebar files this page under %r"
                % (path, labels[1], expected_crumbs[1])
            )
            if expected_crumbs[-1] in RECORD_PAGE_NAMES:
                assert labels[-1] == expected_crumbs[-1], (
                    "%s: last crumb was %r, the record shown is named %r"
                    % (path, labels[-1], expected_crumbs[-1])
                )
            assert labels == expected_crumbs, (
                "%s: full trail was %r, expected %r" % (path, labels, expected_crumbs)
            )
    finally:
        ctx.close()


def test_capability_roadmap_journey_title(browser, live_server, demo_architect):
    """Kept from the original journey: the roadmap page's own title still renders,
    alongside its now-single breadcrumb."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, demo_architect["email"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page).to_have_title("Enterprise Capability Roadmap")
        breadcrumb_nav = page.locator('nav[aria-label="Breadcrumb"], nav[aria-label="breadcrumb"]')
        expect(breadcrumb_nav).to_have_count(1)
    finally:
        ctx.close()
