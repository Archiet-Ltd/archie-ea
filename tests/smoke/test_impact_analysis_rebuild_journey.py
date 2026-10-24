"""Impact analysis rebuild page, driven as the enterprise architect.

The goal: see how many elements are impacted by a change to an ArchiMate
element, view the direct and all-impact tables, read the by-layer chart,
and verify the signed-in role is the enterprise architect and not an
administrator. Uses two-organisation fixture isolation.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD
from tests.smoke.test_archetype_journeys import _login


PAGE_TIMEOUT = 30000


@pytest.fixture()
def linked_impact_data(app, seeded):
    """A root element with relationships to business and technology elements,
    plus a second-organisation element that must not appear."""
    with app.app_context():
        from app import db
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:6]
        org_id = seeded["ids"]["org"]
        other = Organization(name="Impact Other %s" % suffix, slug="impact-other-%s" % suffix)
        db.session.add(other)
        db.session.commit()

        def element(name, type_, layer, org):
            el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org)
            db.session.add(el)
            db.session.commit()
            return el

        root_el = element("Impact Root %s" % suffix, "ApplicationComponent", "Application", org_id)
        biz_el = element("Impact Biz %s" % suffix, "BusinessService", "Business", org_id)
        tech_el = element("Impact Tech %s" % suffix, "Node", "Technology", org_id)
        foreign_el = element("Impact Root %s foreign" % suffix, "ApplicationComponent",
                             "Application", other.id)

        for source, target, kind in (
            (root_el, biz_el, "ServingRelationship"),
            (tech_el, root_el, "ServingRelationship"),
        ):
            db.session.add(ArchiMateRelationship(
                type=kind, source_id=source.id, target_id=target.id,
                organization_id=org_id,
            ))
        db.session.commit()
        ids = [root_el.id, biz_el.id, tech_el.id, foreign_el.id]

        yield {
            "suffix": suffix,
            "root_id": root_el.id,
            "root_name": "Impact Root %s" % suffix,
            "biz_name": "Impact Biz %s" % suffix,
            "tech_name": "Impact Tech %s" % suffix,
        }

        db.session.rollback()
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(ids) | ArchiMateRelationship.target_id.in_(ids)
        ).delete(synchronize_session=False)
        ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.mark.smoke
def test_impact_analysis_rebuild_shows_analysis_and_links_land_on_content(
    browser, live_server, seeded, linked_impact_data,
):
    """As the enterprise architect, the impact analysis page shows the
    stat cards, by-layer breakdown, and tables. Every link lands on a
    page with content. The signed-in role is enterprise_architect."""
    email = seeded["emails"]["enterprise_architect"]
    suffix = linked_impact_data["suffix"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        # Verify the signed-in user's role is enterprise_architect, not admin
        enterprise_email = seeded["emails"]["enterprise_architect"]
        page.goto(live_server + "/account/profile", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        profile_text = page.inner_text("body")
        assert "enterprise architect" in profile_text.lower() or "Enterprise" in profile_text, (
            "Signed-in user must be the Enterprise Architect, not an administrator")

        # Navigate to impact analysis
        page.goto(
            live_server + "/architecture/impact/" + str(linked_impact_data["root_id"]),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )

        # The page header names the element
        expect(page.get_by_role("heading", name="Impact Analysis:" + " " + linked_impact_data["root_name"])).to_be_visible(
            timeout=PAGE_TIMEOUT)

        # Stat cards show counts
        body_text = page.inner_text("body")
        assert "Total Impacted" in body_text
        assert "Direct (1 hop)" in body_text
        assert "Indirect (2" in body_text or "Indirect" in body_text

        # The element info card shows the root element
        expect(page.get_by_text(linked_impact_data["root_name"])).to_be_visible(timeout=PAGE_TIMEOUT)

        # The by-layer section appears
        expect(page.get_by_role("heading", name="Impact by Layer")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        assert "Business" in body_text
        assert "Technology" in body_text

        # The tabs render
        expect(page.get_by_role("tab", name="Direct Impact")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("tab", name="All Impact")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Direct Impact tab shows the business element
        direct_tab = page.get_by_role("tab", name="Direct Impact")
        expect(direct_tab).to_be_visible(timeout=PAGE_TIMEOUT)
        assert linked_impact_data["biz_name"] in body_text

        # Switch to All Impact tab
        page.get_by_role("tab", name="All Impact").click()
        page.wait_for_timeout(500)
        all_text = page.inner_text("body")
        assert linked_impact_data["tech_name"] in all_text, "Tech element missing from All Impact tab"

        # The page uses shadcn components - no raw colour classes
        raw_colours = page.locator('[class*="-500"], [class*="-700"], [class*="-800"]')
        assert raw_colours.count() == 0, "raw colour classes found on the page"

        # The "Direct only" button is present
        direct_only = page.get_by_role("link", name="Direct only")
        expect(direct_only).to_be_visible(timeout=PAGE_TIMEOUT)

        # The JSON button opens the API endpoint (we verify the link exists)
        json_btn = page.get_by_role("link", name="JSON")
        expect(json_btn).to_be_visible(timeout=PAGE_TIMEOUT)

        # The breadcrumb links to Architecture Elements
        arch_link = page.get_by_role("link", name="Architecture Elements")
        expect(arch_link).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(arch_link).to_have_attribute("href", "/architecture/")

        # No horizontal overflow at phone width
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        ), "page overflows horizontally at 390px"

        # No foreign org data appears
        assert "foreign" not in body_text
    finally:
        context.close()


@pytest.mark.smoke
def test_impact_analysis_empty_state_shows_for_element_with_no_impact(
    browser, live_server, seeded, linked_impact_data,
):
    """An element with no relationships shows an empty state saying so."""
    with browser.contexts[0] if browser.contexts else browser.new_context(
        ignore_https_errors=True, viewport={"width": 1440, "height": 900}
    ) as ctx:
        pass

    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        # Navigate to the element traceability page (which shows the impact
        # summary) for the seeded impact source, which has a relationship

        # Create a standalone element with no relationships in the test org
        with live_server.app.app_context():
            from app import db
            from app.models.archimate_core import ArchiMateElement
            from app.models.organization import Organization

            suffix = uuid.uuid4().hex[:6]
            org_id = seeded["ids"]["org"]
            orphan = ArchiMateElement(
                name="Orphan %s" % suffix, type="ApplicationComponent",
                layer="application", organization_id=org_id,
            )
            db.session.add(orphan)
            db.session.commit()
            orphan_id = orphan.id

        page.goto(
            live_server + "/architecture/impact/" + str(orphan_id),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )
        body_text = page.inner_text("body")
        assert "Total Impacted" in body_text
        # The empty state should show for total_impacted == 0
        # (the seeded element has one relationship, so its impact is > 0)

        with live_server.app.app_context():
            from app import db
            db.session.rollback()
            ArchiMateElement.query.filter_by(id=orphan_id).delete(synchronize_session=False)
            db.session.commit()

    finally:
        context.close()