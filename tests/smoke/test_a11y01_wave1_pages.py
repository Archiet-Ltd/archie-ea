"""axe-core on the three wave 1 pages brief a11y01 names.

/applications/                          link-in-text-block
/implementation/work-packages/<id>      progressbar-name
/integrations/connectors                nested-interactive

A Playwright test opens each page signed in, in an organisation seeded with
linked data (a work package under way with a recorded progress, two connectors
with sync history), runs the shared axe helper (axe_helpers.py, which takes the
audit's tag set) and fails on the three named rules and on any other serious or
critical violation, naming the rule and the selector. Light and dark, at 1440
and 390 wide; every dark case asserts the page really renders dark. Red before
the fix, green after.

The applications list asserts its evidence links before axe runs. At 390 wide the
list is a card layout, and axe does not apply link-in-text-block to it even when
the footer is unfixed (the base passes there), so axe alone proves nothing for
that rule at that width. The case therefore asserts what a person gets instead:
at every width the links are on the page and underlined, distinct by more than
colour.
"""

import datetime

import pytest
from playwright.sync_api import expect

from .axe_helpers import _axe_violations, assert_theme, new_context
from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_a11y04_detail_pages import VIEWPORTS, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

RULES = ["link-in-text-block", "aria-progressbar-name", "nested-interactive"]

EVIDENCE_LINKS = "p[data-testid='result-footer-evidence'] > a"

PAGES = [
    ("application_manager", "applications list",
     lambda base, ids: "%s/applications/" % base, EVIDENCE_LINKS + ":visible"),
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


def _blocking_findings(page):
    return [
        "%s [%s] at %s" % (v["id"], v.get("impact"), _selector(n))
        for v in _axe_violations(page, rules=RULES, impacts=("serious", "critical"))
        for n in (v.get("nodes") or [{}])
    ]


def _assert_evidence_links_are_distinct(page, width):
    """The applications list shows its evidence links, each underlined (not told apart by colour)."""
    assert page.locator(EVIDENCE_LINKS).count() > 0, (
        "no evidence links on /applications/ at %d wide: axe would pass an empty footer" % width)
    plain = page.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel))
            .filter(a => a.offsetParent !== null)
            .filter(a => !getComputedStyle(a).textDecorationLine.includes('underline'))
            .map(a => a.textContent.trim())""",
        EVIDENCE_LINKS,
    )
    visible = page.evaluate(
        "(sel) => Array.from(document.querySelectorAll(sel)).filter(a => a.offsetParent !== null).length",
        EVIDENCE_LINKS,
    )
    assert visible > 0, "the evidence links on /applications/ are all hidden at %d wide" % width
    assert not plain, "evidence links not underlined at %d wide: %s" % (width, "; ".join(plain))


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
    ctx = new_context(browser, width, height, dark)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"][archetype])
        response = page.goto(url(live_server, ids), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, "%s returned %s" % (page_name, response.status)
        page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_be_visible()
        assert_theme(page, dark, "%s (%s)" % (page_name, label))
        if page_name == "applications list":
            _assert_evidence_links_are_distinct(page, width)
        failures = _blocking_findings(page)
    finally:
        ctx.close()
    assert not failures, "%s (%s): %s" % (page_name, label, "; ".join(failures))
