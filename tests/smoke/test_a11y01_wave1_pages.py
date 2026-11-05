"""axe-core on the three wave 1 pages brief a11y01 names.

/applications/                          link-in-text-block
/implementation/work-packages/<id>      progressbar-name
/integrations/connectors                nested-interactive

A Playwright test opens each page signed in, in an organisation seeded with
linked data (a work package under way with a recorded progress, two connectors
with sync history), runs the audit's axe helper and fails on the three named
rules and on any other serious or critical violation, naming the rule and the
selector. Light and dark, at 1440 and 390 wide. Red before the fix, green after.
"""

import datetime

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_a11y04_detail_pages import TAGS, VIEWPORTS, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

RULES = ["link-in-text-block", "aria-progressbar-name", "nested-interactive"]

PAGES = [
    ("application_manager", "applications list",
     lambda base, ids: "%s/applications/" % base, "main"),
    ("enterprise_architect", "work package",
     lambda base, ids: "%s/implementation/work-packages/%d" % (base, ids["work_package"]),
     "[data-testid='wp-progress']"),
    ("platform_admin", "connector dashboard",
     lambda base, ids: "%s/integrations/connectors" % base,
     "text=Jira projects"),
]


def _selector(node):
    target = node.get("target") or []
    return " ".join(t if isinstance(t, str) else "/".join(t) for t in target)


def _axe_failures(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    return [
        "%s [%s] at %s" % (v["id"], v.get("impact"), _selector(n))
        for v in data.get("violations", [])
        if v["id"] in RULES or v.get("impact") in ("serious", "critical")
        for n in (v.get("nodes") or [{}])
    ]


@pytest.fixture(scope="module")
def a11y01_data(seeded):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        today = datetime.date.today()
        wp = UnifiedWorkPackage(
            name="Accessibility rollout", organization_id=org_id, status="in_progress",
            progress_percentage=40, start_date=today - datetime.timedelta(days=20),
            end_date=today + datetime.timedelta(days=40),
        )
        db.session.add(wp)
        for name, kind in (("Jira projects", "jira"), ("Code hosting", "github")):
            db.session.add(ConnectorConfig(
                organization_id=org_id, connector_type=kind, name=name,
                config={}, status="active"))
        db.session.commit()
        wp_id = wp.id
        db.session.remove()
    return {"work_package": wp_id}


@pytest.mark.parametrize("archetype,page_name,url,ready", PAGES)
@pytest.mark.parametrize("label,width,height,dark", VIEWPORTS)
def test_wave1_pages_have_no_serious_axe_failure(
    browser, live_server, seeded, a11y01_data, archetype, page_name, url, ready,
    label, width, height, dark,
):
    ids = dict(seeded["ids"])
    ids.update(a11y01_data)
    ctx = browser.new_context(viewport={"width": width, "height": height})
    if dark:
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"][archetype])
        response = page.goto(url(live_server, ids), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, "%s returned %s" % (page_name, response.status)
        page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_be_visible()
        failures = _axe_failures(page)
    finally:
        ctx.close()
    assert not failures, "%s (%s): %s" % (page_name, label, "; ".join(failures))
