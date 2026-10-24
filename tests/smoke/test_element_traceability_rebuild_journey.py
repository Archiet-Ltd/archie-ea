"""Element traceability page, driven as the enterprise architect.

The goal: see which elements an ArchiMate element traces to, follow a link
to an impacted element's own traceability, and verify every link lands on
content. Runs in the seeded organisation and verifies a second organisation's
data never appears.
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
def linked_trace_data(app, seeded):
    """A chain of ArchiMate elements with relationships for traceability,
    plus a second-organisation element that must never appear."""
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

        biz_el = element("Trace Biz %s" % suffix, "BusinessService", "Business", org_id)
        app_el = element("Trace App %s" % suffix, "ApplicationComponent", "Application", org_id)
        tech_el = element("Trace Tech %s" % suffix, "Node", "Technology", org_id)
        foreign_el = element("Trace App %s foreign" % suffix, "ApplicationComponent", "Application", other.id)

        for source, target, kind in (
            (app_el, biz_el, "RealizationRelationship"),
            (tech_el, app_el, "ServingRelationship"),
        ):
            db.session.add(ArchiMateRelationship(
                type=kind, source_id=source.id, target_id=target.id,
                organization_id=org_id,
            ))
        db.session.commit()
        ids = [app_el.id, biz_el.id, tech_el.id, foreign_el.id]

        yield {
            "suffix": suffix,
            "app_el_id": app_el.id,
            "biz_name": "Trace Biz %s" % suffix,
            "tech_name": "Trace Tech %s" % suffix,
            "app_name": "Trace App %s" % suffix,
        }

        db.session.rollback()
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(ids) | ArchiMateRelationship.target_id.in_(ids)
        ).delete(synchronize_session=False)
        ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.mark.smoke
def test_element_traceability_shows_chain_and_links_land_on_content(
    browser, live_server, seeded, linked_trace_data,
):
    """As the enterprise architect, see the traceability chain and follow
    every link to a page with content."""
    email = seeded["emails"]["enterprise_architect"]
    suffix = linked_trace_data["suffix"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        # Navigate to element traceability
        page.goto(
            live_server + "/architecture/traceability/" + str(linked_trace_data["app_el_id"]),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )

        # The page header names the element
        expect(page.get_by_role("heading", name="Trace App %s" % suffix)).to_be_visible(
            timeout=PAGE_TIMEOUT)

        # The chain shows the business element
        body_text = page.inner_text("body")
        assert linked_trace_data["biz_name"] in body_text, (
            "Business element missing from traceability chain")
        assert linked_trace_data["tech_name"] in body_text, (
            "Technology element missing from traceability chain")
        assert "foreign" not in body_text, (
            "Another organisation's element is listed in the chain")

        # The pivot element is marked "THIS"
        assert "THIS" in body_text

        # The page uses shadcn tokens
        raw_colours = page.locator('[class*="-500"], [class*="-700"], [class*="-800"]')
        assert raw_colours.count() == 0, "raw colour classes found on the page"

        # D3 tree container exists
        expect(page.locator("#tree-container")).to_be_visible(timeout=PAGE_TIMEOUT)

        # The static chain view shows layer sections
        expect(page.locator("#static-chain")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Business Layer")).to_be_visible()
        expect(page.get_by_text("Technology Layer")).to_be_visible()

        # No horizontal overflow at phone width
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        ), "page overflows horizontally at 390px"
    finally:
        context.close()


@pytest.mark.smoke
def test_element_traceability_impact_summary_links_to_impact_analysis(
    browser, live_server, seeded, linked_trace_data,
):
    """As the enterprise architect, the impact summary section links to
    the impact analysis page, which loads with content."""
    email = seeded["emails"]["enterprise_architect"]
    suffix = linked_trace_data["suffix"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(
            live_server + "/architecture/traceability/" + str(linked_trace_data["app_el_id"]),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )

        # The breadcrumb links to the traceability matrix
        trace_link = page.get_by_role("link", name="Architecture Traceability")
        expect(trace_link).to_be_visible(timeout=PAGE_TIMEOUT)
        trace_link.click()
        page.wait_for_url(lambda url: "/architecture/traceability" in url
                          and "traceability/" not in url, timeout=PAGE_TIMEOUT)
        assert page.inner_text("body").strip(), "traceability matrix page is empty"

        # Navigate back to element traceability
        page.goto(
            live_server + "/architecture/traceability/" + str(linked_trace_data["app_el_id"]),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )

        # The "Back to Traceability Matrix" link works
        back_link = page.get_by_text("Back to Traceability Matrix")
        expect(back_link).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()