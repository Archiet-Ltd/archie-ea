"""Cross-layer traceability matrix, driven as an enterprise architect.

The goal: see which business and motivation elements an application traces up
to, follow the application to its own chain, narrow the list by name, and be
told plainly when nothing matches. Built from linked data in one organisation
with a lookalike in a second one, which must never appear.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture()
def linked_chain(app, seeded):
    """An application realised-by/serving a business service and a goal, in the
    seeded organisation, plus a same-named application in a second one."""
    with app.app_context():
        from app import db
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:6]
        org_id = seeded["ids"]["org"]
        other = Organization(name="Trace Other %s" % suffix, slug="trace-other-%s" % suffix)
        db.session.add(other)
        db.session.commit()

        def element(name, type_, layer, org):
            el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org)
            db.session.add(el)
            db.session.commit()
            return el

        app_el = element("Tracer Ledger %s" % suffix, "ApplicationComponent", "Application", org_id)
        biz = element("Tracer Billing Service %s" % suffix, "BusinessService", "Business", org_id)
        goal = element("Tracer Faster Close %s" % suffix, "Goal", "Motivation", org_id)
        foreign = element("Tracer Ledger %s foreign" % suffix, "ApplicationComponent", "Application", other.id)
        for source, target, kind in ((app_el, biz, "RealizationRelationship"), (goal, app_el, "InfluenceRelationship")):
            db.session.add(ArchiMateRelationship(type=kind, source_id=source.id, target_id=target.id,
                                                 organization_id=org_id))
        db.session.commit()
        ids = [app_el.id, biz.id, goal.id, foreign.id]
        chain = {"suffix": suffix, "app_id": app_el.id}
        yield chain
        db.session.rollback()
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(ids) | ArchiMateRelationship.target_id.in_(ids)
        ).delete(synchronize_session=False)
        ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.mark.smoke
def test_traceability_matrix_shows_chain_and_links_land_on_content(browser, live_server, seeded, linked_chain):
    email = seeded["emails"]["enterprise_architect"]
    suffix = linked_chain["suffix"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(live_server + "/architecture/traceability?search=Tracer+Ledger+" + suffix,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The matrix shows the application with the elements it traces to.
        rows = page.locator("#matrix-view tbody tr")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        row = rows.first
        expect(row).to_contain_text("Tracer Ledger %s" % suffix)
        expect(row).to_contain_text("Tracer Billing Service %s" % suffix)
        expect(row).to_contain_text("Tracer Faster Close %s" % suffix)
        assert "foreign" not in page.inner_text("#matrix-view"), "another organisation's element is listed"

        # Built from the component library: no palette colours, no native selects.
        assert page.locator("select").count() == 0
        assert page.locator('[class*="-500"], [class*="-700"], [class*="-800"]').count() == 0

        # The current direction and the current pivot are marked, and switching works.
        expect(page.get_by_role("link", name="Top-Down")).to_have_attribute("aria-current", "true")
        page.get_by_role("link", name="Bottom-Up").click()
        page.wait_for_url(lambda url: "direction=inverse" in url, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("link", name="Bottom-Up")).to_have_attribute("aria-current", "true")
        # No technology nodes are modelled here, so the page says so and offers the way forward.
        expect(page.locator("#matrix-view").get_by_role("link", name="Add ArchiMate Elements")).to_be_visible()

        # The application name leads to its own chain, which names the linked elements.
        page.goto(live_server + "/architecture/traceability?search=Tracer+Ledger+" + suffix,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#matrix-view").get_by_role("link", name="Tracer Ledger %s" % suffix).click()
        page.wait_for_url(lambda url: "/architecture/traceability/%s" % linked_chain["app_id"] in url,
                          timeout=PAGE_TIMEOUT)
        body = page.inner_text("body")
        assert "Tracer Billing Service %s" % suffix in body
        assert "Tracer Faster Close %s" % suffix in body

        # The Sankey view is one click away and shows something, not an empty frame.
        page.goto(live_server + "/architecture/traceability", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#matrix-view")).to_be_visible()
        page.get_by_role("tab", name="Sankey").click()
        expect(page.locator("#sankey-view")).to_be_visible()
        expect(page.locator("#matrix-view")).to_be_hidden()
        expect(page.locator("#sankey-container svg, #sankey-container p").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The coverage panel opens and its orphan links land on an element chain.
        page.get_by_role("button", name="Show gap panel").click()
        expect(page.locator("#gap-panel")).to_be_visible()
        orphan = page.locator('#gap-panel a[href*="/architecture/traceability/"]').first
        if orphan.count():
            orphan.click()
            page.wait_for_url(lambda url: "/architecture/traceability/" in url, timeout=PAGE_TIMEOUT)
            assert page.inner_text("main, body").strip()
    finally:
        context.close()


@pytest.mark.smoke
def test_traceability_matrix_empty_search_says_why_and_offers_next_step(browser, live_server, seeded):
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(live_server + "/architecture/traceability?search=zz-no-such-element-zz",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No ApplicationComponent elements found.")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Add ArchiMate elements to build the traceability matrix.")).to_be_visible()
        page.get_by_role("link", name="Clear").click()
        page.wait_for_url(lambda url: "search=" not in url, timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Add ArchiMate Elements").count()
        # No horizontal page overflow at phone width.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        context.close()
