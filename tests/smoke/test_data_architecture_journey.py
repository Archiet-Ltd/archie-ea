"""Journey, in a browser, as the data architect: reading the data architecture overview.

They open the page from the sidebar, find which entry is marked, read four figures (data
entities, holding personal data, lineage flows, not yet traced), click each and find a list
with the same number of rows. Nothing promises a feature that is not there, no figure is a
bare zero, and on a phone no text in the page is cut off. A second organisation sees only its
own figures, and an organisation with nothing recorded is told why each figure is zero.
"""

import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "data_architect"
WALK = os.environ.get("SMOKE_SCREENSHOT_DIR")


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id):
    """Customer and Customer profile (both personal data) with a recorded flow between them
    (plus the CRM writing Customer, a second relationship the lineage counts),
    and Invoice, which no flow touches. The CRM writes Customer."""
    from flask import g

    from app import create_app, db
    from app.models.all_missing_models import DataLineage
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import DataDomain, DataEntity
    from app.modules.architecture.services.data_architecture_service import record_entity_access

    app = create_app("testing")
    with app.app_context():
        tag = uuid.uuid4().hex[:5]
        with app.test_request_context("/"):
            g.current_org_id = org_id
            domain = DataDomain(name="Customer data %s" % tag, organization_id=org_id)
            db.session.add(domain)
            db.session.flush()
            customer = DataEntity(name="Customer %s" % tag, domain_id=domain.id, organization_id=org_id, contains_pii=True)
            profile = DataEntity(name="Customer profile %s" % tag, domain_id=domain.id, organization_id=org_id, contains_pii=True)
            invoice = DataEntity(name="Invoice %s" % tag, domain_id=domain.id, organization_id=org_id)
            crm = ApplicationComponent(name="CRM %s" % tag, organization_id=org_id)
            db.session.add_all([customer, profile, invoice, crm])
            db.session.flush()
            record_entity_access(customer, crm, ["C", "R", "U"])
            db.session.add(DataLineage(name="nightly copy", organization_id=org_id, lineage_type="Batch",
                                       archimate_element_id=customer.archimate_element_id,
                                       target_archimate_element_id=profile.archimate_element_id))
            db.session.commit()
        db.session.remove()
    return tag


@pytest.fixture
def orgs():
    rich = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
    rich["tag"] = _seed(rich["org_id"])
    empty = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
    return rich, empty


def _card(page, testid):
    return page.get_by_test_id(testid)


def _value(page, testid):
    return int(page.get_by_test_id(testid + "-value").inner_text().strip())


def _clipped(page):
    """Texts in the page's main column that are cut off by their own box."""
    return page.evaluate("""() => {
        const main = document.querySelector('main') || document.body;
        const out = [];
        for (const el of main.querySelectorAll('*')) {
            if (!el.children.length && el.textContent.trim() && el.clientWidth > 1 && el.scrollWidth > el.clientWidth + 1
                && getComputedStyle(el).overflowX !== 'visible' && el.offsetParent !== null) {
                out.push(el.textContent.trim().slice(0, 40));
            }
        }
        return out;
    }""")


def test_data_architect_reads_the_overview_and_every_figure_opens_its_list(page, live_server, orgs):
    rich, _ = orgs
    fresh_org.sign_in(page, live_server, rich["emails"][ARCHITECT])
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name=re.compile("Systems")).first.click()
    page.get_by_role("link", name="Data register").first.click()
    page.wait_for_url(lambda url: "/architecture/data-entities" in url, timeout=PAGE_TIMEOUT)
    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _shot(page, "data-architecture-1440-light")

    # One sidebar entry is marked as the page the person is on.
    marked = page.locator('[data-slot="sidebar"] [aria-current="page"], [data-sidebar="content"] [aria-current="page"]')
    assert marked.count() == 1, marked.count()

    assert page.get_by_role("heading", level=1).inner_text().strip() == "Data architecture"
    body = page.locator("main").inner_text()
    assert "Coming soon" not in body
    assert "DATA-STORAGE" not in body
    assert page.locator('[data-testid="data-stat-row"] [data-slot="card"]').count() == 4

    entities, personal, flows, untraced = (_value(page, t) for t in
                                           ("data-entities-card", "personal-data-card", "lineage-flows-card", "untraced-card"))
    assert (entities, personal, flows, untraced) == (3, 2, 2, 1)

    def rows_after_click(testid, row_selector, url_part):
        page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id(testid + "-link").click()
        page.wait_for_url(lambda url: url_part in url, timeout=PAGE_TIMEOUT)
        page.locator(row_selector).first.wait_for(timeout=PAGE_TIMEOUT)
        return page.locator(row_selector).count()

    assert rows_after_click("data-entities-card", "tr[data-testid^=data-entity-row-]", "/architecture/data-entities") == entities
    assert rows_after_click("personal-data-card", "tr[data-testid^=data-entity-row-]", "personal=1") == personal
    assert page.get_by_test_id("catalog-personal-note").is_visible()

    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("lineage-flows-card-link").click()
    page.wait_for_url(lambda url: "/architecture/data-lineage" in url, timeout=PAGE_TIMEOUT)
    assert int(page.get_by_test_id("lineage-edge-count").inner_text()) == flows
    assert int(page.get_by_test_id("lineage-untraced-count").inner_text()) == untraced

    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("untraced-card-link").click()
    page.wait_for_url(lambda url: url.endswith("#lineage-untraced"), timeout=PAGE_TIMEOUT)
    untraced_box = page.get_by_test_id("lineage-untraced")
    assert "Invoice %s" % rich["tag"] in untraced_box.inner_text()

    # The primary action opens the entity register.
    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("main").get_by_role("link", name="See every entity").first.click()
    page.wait_for_url(lambda url: "/architecture/data-entities" in url, timeout=PAGE_TIMEOUT)

    # The models page has its own title, and explains its zeros.
    page.goto(live_server + "/enterprise/data/models", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.get_by_role("heading", level=1).inner_text().strip() == "Data models"
    assert "None recorded yet" in page.get_by_test_id("conceptual-models-card-why").inner_text()


def test_overview_fits_a_phone_and_shows_the_same_figures(page, live_server, orgs):
    rich, _ = orgs
    fresh_org.sign_in(page, live_server, rich["emails"][ARCHITECT])
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert _value(page, "data-entities-card") == 3
    _shot(page, "data-architecture-390-light")
    assert _clipped(page) == []


def test_organisation_with_nothing_recorded_is_told_why_each_figure_is_zero(page, live_server, orgs):
    rich, empty = orgs
    fresh_org.sign_in(page, live_server, empty["emails"][ARCHITECT])
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for testid in ("data-entities-card", "personal-data-card", "lineage-flows-card", "untraced-card"):
        assert _value(page, testid) == 0
        assert page.get_by_test_id(testid + "-why").inner_text().strip()
    # Not another organisation's entity.
    assert rich["tag"] not in page.locator("main").inner_text()
    page.get_by_test_id("data-entities-card-link").click()
    page.wait_for_url(lambda url: "/architecture/data-entities/create" in url, timeout=PAGE_TIMEOUT)
    _shot(page, "data-architecture-empty-1440-light")
