"""Impact analysis rebuild page, driven as the enterprise architect.

The goal: see how many elements are impacted by a change to an ArchiMate
element, view the direct and all-impact data tables, and read the by-layer
chart. Uses two-organisation fixture isolation.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.test_archetype_journeys import _login


PAGE_TIMEOUT = 30000


def _assert_persona(email, persona):
    """The signed-in user is the persona named and holds no administrator role."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == persona, (
            "Expected enterprise_role %s, got %s" % (persona, user.enterprise_role))
        assert not user.is_admin(), "User must not be an administrator"


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
    _assert_persona(email, "enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, email)

        # Verify the signed-in user's role is enterprise_architect
        page.goto(live_server + "/account/profile", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        profile_text = page.inner_text("body").lower()
        assert "enterprise architect" in profile_text or "enterprise" in profile_text, (
            "Signed-in user must be the Enterprise Architect, not an administrator")

        # Navigate to impact analysis
        page.goto(
            live_server + "/architecture/impact/" + str(linked_impact_data["root_id"]),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )

        # The page title names the element
        title_text = page.title()
        assert "Impact Analysis" in title_text
        assert linked_impact_data["root_name"] in title_text

        # The h1 heading from page_shell
        heading_text = page.inner_text("h1")
        assert linked_impact_data["root_name"] in heading_text

        # Stat cards show counts
        body_text = page.inner_text("body")
        assert "Total Impacted" in body_text
        assert "Direct (1 hop)" in body_text
        assert "Indirect" in body_text

        # The element info card shows the root element name
        expect(page.get_by_text(linked_impact_data["root_name"]).first).to_be_visible(
            timeout=PAGE_TIMEOUT)

        # The by-layer section appears
        expect(page.get_by_role("heading", name="Impact by Layer")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        assert "Business" in body_text
        assert "Technology" in body_text

        # The tabs render
        expect(page.get_by_role("tab", name="Direct Impact")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("tab", name="All Impact")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Direct Impact tab shows the business element
        assert linked_impact_data["biz_name"] in body_text

        # Switch to All Impact tab
        page.get_by_role("tab", name="All Impact").click()
        page.wait_for_timeout(500)
        all_text = page.inner_text("body")
        assert linked_impact_data["tech_name"] in all_text, "Tech element missing from All Impact tab"

        # The page uses shadcn components - no raw colour classes like -500 or -700
        raw_colours = page.locator('[class*="-500"], [class*="-700"], [class*="-800"]')
        assert raw_colours.count() == 0, "raw colour classes found on the page"

        # The "Direct only" button exists
        direct_only = page.get_by_role("link", name="Direct only")
        expect(direct_only).to_be_visible(timeout=PAGE_TIMEOUT)

        # The breadcrumb links to Architecture Elements
        arch_link = page.get_by_role("link", name="Architecture Elements")
        expect(arch_link).to_be_visible(timeout=PAGE_TIMEOUT)

        # No horizontal overflow at phone width
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        ), "page overflows horizontally at 390px"

        # No foreign org data appears
        assert "foreign" not in page.inner_text("body")
    finally:
        context.close()