"""axe-core WCAG 2.2 AA on the three detail pages brief a11y04 names.

QA-061: the application record's tab strip — each tab button's accessible
name starts with its visible label.
QA-062: the technology element detail page — definition lists hold only
dt/dd groups, and links in running text are underlined, not told apart by
colour alone.
QA-063: the business model canvas — the edit and remove targets on a box
entry are at least 24 by 24 CSS pixels.

A Playwright test opens each page signed in, in an organisation seeded with
linked data, runs axe-core and asserts none of the five rules fires:
label-content-name-mismatch, definition-list, dlitem, link-in-text-block,
target-size. Light and dark, at 1440 and 390 wide. Red before the fix,
green after it.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The five rules brief a11y04 names. The audit's tag set (wcag2a, wcag2aa,
# wcag21a, wcag21aa, wcag22aa) selects all of them, so one axe run per page
# covers all five; the assertion below filters on rule id.
RULES = [
    "label-content-name-mismatch",
    "definition-list",
    "dlitem",
    "link-in-text-block",
    "target-size",
]

TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]

# (archetype, page, url builder, seeded id key, ready selector)
PAGES = [
    (
        "application_manager",
        "application record",
        lambda base, ids: "%s/applications/%d" % (base, ids["application"]),
        "[role=tablist]",
    ),
    (
        "enterprise_architect",
        "technology element",
        lambda base, ids: "%s/architecture/technology/Node/%d" % (base, ids["radar_element"]),
        "[data-testid='about-card']",
    ),
    (
        "business_architect",
        "business model canvas",
        lambda base, ids: "%s/business-model/%d" % (base, ids["canvas"]),
        "[data-canvas='business-model-canvas']",
    ),
]

VIEWPORTS = [
    ("1440 light", 1440, 900, False),
    ("1440 dark", 1440, 900, True),
    ("390 light", 390, 800, False),
    ("390 dark", 390, 800, True),
]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _axe_violations(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    return [v for v in data.get("violations", []) if v["id"] in RULES]


@pytest.fixture(scope="module")
def a11y04_data(seeded):
    """Seed the linked data the three pages need:

    - a business model canvas with a real entry in its Key Partnerships box,
      so the box's edit and remove buttons render (target-size);
    - an archimate relationship from the seeded application's element, so the
      application record's Architecture tab shows a count and its tab button's
      visible label ("Architecture 1") outgrows its aria-label ("Architecture")
      (label-content-name-mismatch).
    """
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_model import BusinessModelCanvas
    from app.services.archimate_backbone import create_backbone_element
    from app.modules.business_model_canvas import model_service

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        app_row = ApplicationComponent.query.get(seeded["ids"]["application"])
        element = ArchiMateElement.query.get(app_row.archimate_element_id)
        target = (
            ArchiMateElement.query.filter_by(
                organization_id=org_id, type="Node", layer="technology",
            ).first()
        )
        db.session.add(
            ArchiMateRelationship(
                type="association",
                source_id=element.id,
                target_id=target.id,
                organization_id=org_id,
            )
        )
        canvas = BusinessModelCanvas(name="Accessibility canvas", organization_id=org_id)
        db.session.add(canvas)
        db.session.flush()
        actor = create_backbone_element(
            element_type="BusinessActor", layer="business", name="Smoke partner",
            organization_id=org_id,
            provenance={"created_from": "a11y04 smoke fixture"},
        )
        db.session.commit()
        model_service.add_entry(canvas, "key_partners", element_id=actor.id)
        db.session.commit()
        canvas_id = canvas.id
        db.session.remove()
    return {"canvas": canvas_id}


def _audit_page(browser, live_server, emails, ids, archetype, page_name, url, ready, width, height, dark):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    if dark:
        # tailwind.config.js: darkMode: ["class"] — set the class directly so
        # the page is audited in dark mode; no theme toggle exists on these
        # pages yet, so this proves the rendered markup under the .dark class.
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    try:
        _login(page, live_server, emails[archetype])
        response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, "%s returned %s" % (page_name, response.status)
        page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_be_visible()
        violations = _axe_violations(page)
    finally:
        ctx.close()
    return violations


@pytest.mark.parametrize("archetype,page_name,url,ready", PAGES)
@pytest.mark.parametrize("label,width,height,dark", VIEWPORTS)
def test_five_rules_do_not_fire(
    browser, live_server, seeded, a11y04_data, archetype, page_name, url, ready,
    label, width, height, dark,
):
    ids = dict(seeded["ids"])
    ids["canvas"] = a11y04_data["canvas"]
    violations = _audit_page(
        browser, live_server, seeded["emails"], ids, archetype, page_name,
        url(live_server, ids), ready, width, height, dark,
    )
    assert not violations, (
        "%s (%s, %s): axe reports %d violation(s) of the five a11y04 rules: %s"
        % (page_name, label, "dark" if dark else "light", len(violations),
           "; ".join("%s (%d element%s)" % (v["id"], len(v.get("nodes") or []),
                                            "" if len(v.get("nodes") or []) == 1 else "s")
                     for v in violations)))
