"""A business architect opens the Business Model Canvas of a seeded company and
finds every box filled from the model; adds a segment, a price and a partner,
sees the segment on the Value Proposition Canvas, and follows every entry to
the element it names. A second, empty canvas says why each box is empty.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
expect.set_options(timeout=PAGE_TIMEOUT)
BOXES = (
    "key-partners", "key-activities", "key-resources", "value-propositions",
    "customer-relationships", "channels", "customer-segments", "cost-structure", "revenue-streams",
)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _assert_persona(email, role="business_architect"):
    """The signed-in user is the persona the journey names, not an administrator or platform role."""
    from app import create_app
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == role, user.enterprise_role
        assert not is_platform_admin(user)
        assert not rbac_service.is_org_admin(user, user.organization_id)


def _count_elements(name, org_id):
    from app import create_app
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        return ArchiMateElement.query.filter_by(name=name, organization_id=org_id).count()


def _seed_canvases(org_id):
    """A filled canvas (elements, a price, an application with a recorded cost) and an empty one."""
    from app import create_app, db
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.services import cost_fact_store

    app = create_app("testing")
    with app.app_context():
        filled = BusinessModelCanvas(name="Journey Canvas", organization_id=org_id)
        empty = BusinessModelCanvas(name="Empty Journey Canvas", organization_id=org_id)
        db.session.add_all([filled, empty])
        db.session.flush()
        _seed_canvas_model(org_id, filled)
        if not ArchiMateElement.query.filter_by(name="Existing logistics firm", organization_id=org_id).count():
            db.session.add(ArchiMateElement(
                name="Existing logistics firm", type="BusinessActor", layer="business", organization_id=org_id,
            ))
        app_row = ApplicationComponent(name="Journey Billing Platform", organization_id=org_id)
        db.session.add(app_row)
        db.session.flush()
        cost_fact_store.upsert_fact(org_id, "application", app_row.id, 120000, "GBP", "journey")
        db.session.commit()
        return filled.id, empty.id


@pytest.mark.smoke
@pytest.mark.journey
def test_business_architect_fills_and_follows_the_canvas(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    filled_id, empty_id = _seed_canvases(seeded["ids"]["org"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _login(page, live_server, email)
        canvas_url = f"{live_server}/business-model/{filled_id}"
        page.goto(canvas_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Every box has model content, and every entry names the element it opens.
        hrefs = {}
        for box in BOXES:
            notes = page.locator(f'[data-canvas-notes="{box}"] > li')
            assert notes.count() >= 1, f"{box} is empty on a seeded company"
            for i in range(notes.count()):
                link = notes.nth(i).locator("a")
                assert link.count() == 1, f"{box} entry {i} does not open anything"
                hrefs[link.get_attribute("href")] = link.inner_text()
        assert len(hrefs) >= 20
        for href, text in hrefs.items():
            response = context.request.get(live_server + href)
            assert response.status == 200, (href, response.status)
            assert text.split(":")[0].strip() in response.text(), f"{href} does not show {text}"

        # The cost box shows the recorded cost, and revenue shows the recorded price.
        expect(page.locator('[data-canvas-notes="cost-structure"]')).to_contain_text("Journey Billing Platform: 120,000 GBP a year")
        expect(page.locator('[data-canvas-notes="revenue-streams"]')).to_contain_text("1,850.00 GBP per use")
        expect(page.get_by_test_id("bmc-vision")).to_contain_text("Recover every sensor assembly for reuse")

        # Add a segment through the box's own control; it survives a reload.
        page.get_by_role("button", name="Add to Customer Segments").click()
        page.get_by_test_id("bmc-name-customer_segments").fill("Grocery chains")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("bmc-add-submit-customer_segments").click()
        expect(page.locator('[data-canvas-notes="customer-segments"]')).to_contain_text("Grocery chains")
        page.reload()
        expect(page.locator('[data-canvas-notes="customer-segments"]')).to_contain_text("Grocery chains")

        # An element the model already holds is offered first, and choosing it adds that
        # element to the box instead of creating a second one.
        page.get_by_role("button", name="Add to Key Partners").click()
        picker = page.get_by_test_id("bmc-pick-key_partners")
        assert "Existing logistics firm (BusinessActor)" in picker.locator("option").all_inner_texts()
        picker.select_option(label="Existing logistics firm (BusinessActor)")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("bmc-add-submit-key_partners").click()
        expect(page.locator('[data-canvas-notes="key-partners"]')).to_contain_text("Existing logistics firm")
        assert _count_elements("Existing logistics firm", seeded["ids"]["org"]) == 1

        # Add a price to a value proposition.
        page.get_by_role("button", name="Add to Revenue Streams").click()
        page.fill("#price-name", "Pilot licence")
        page.fill("#price-amount", "5400")
        page.select_option("#price-period", "month")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("bmc-add-submit-revenue_streams").click()
        expect(page.locator('[data-canvas-notes="revenue-streams"]')).to_contain_text("Pilot licence, 5,400.00 GBP per month")

        # The list view holds the same entries as the drawing.
        page.get_by_role("button", name="List", exact=True).click()
        expect(page.get_by_role("table", name=re.compile("list view"))).to_contain_text("Grocery chains")
        page.get_by_role("button", name="Canvas", exact=True).click()

        # The new segment is on the Value Proposition Canvas, linked to its element.
        page.get_by_role("link", name="Value Proposition Canvas").first.click()
        page.wait_for_url(re.compile(r"/value-proposition$"), timeout=PAGE_TIMEOUT)
        segment = page.get_by_test_id("vpc-segments").get_by_role("link", name="Grocery chains")
        expect(segment).to_be_visible()
        expect(page.locator('[data-canvas-notes="gain-creators"]')).to_contain_text("Operators know their readings can be trusted")
        segment.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).to_contain_text("Grocery chains")

        # Removing an entry takes it off the canvas and keeps the element.
        page.goto(canvas_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_role("button", name="Remove Grocery chains from this canvas").click()
        expect(page.locator('[data-canvas-notes="customer-segments"]')).not_to_contain_text("Grocery chains")
        assert context.request.get(live_server + [h for h, t in hrefs.items()][0]).status == 200

        # An empty canvas says why each box is empty and what to do next.
        page.goto(f"{live_server}/business-model/{empty_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Cost Structure reads the organisation's recorded application costs, so it is not empty here.
        expect(page.locator('[data-canvas-notes="cost-structure"]')).to_contain_text("Journey Billing Platform")
        for box in (b for b in BOXES if b != "cost-structure"):
            empty = page.locator(f'[data-canvas-empty="{box}"]')
            expect(empty).to_be_visible()
            assert len(empty.inner_text()) > 20
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_canvas_keeps_its_nine_boxes_at_phone_width(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    filled_id, _ = _seed_canvases(seeded["ids"]["org"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(f"{live_server}/business-model/{filled_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        region = page.get_by_role("region", name=re.compile("scroll horizontally"))
        assert region.evaluate("e => e.scrollWidth > e.clientWidth"), "the canvas must scroll sideways, not stack"
        assert region.evaluate("e => e.scrollWidth") >= 900, "the nine boxes keep their natural width"
        narrowest = page.locator("[data-canvas-notes]").evaluate_all(
            "els => Math.min(...els.map(e => e.closest('[data-slot=card]').getBoundingClientRect().width))"
        )
        assert narrowest >= 120, f"a box is squeezed to {narrowest}px"
        tops = {
            box: page.locator(f'[data-canvas-notes="{box}"]').evaluate("e => e.closest('[data-slot=card]').getBoundingClientRect().top")
            for box in ("key-partners", "value-propositions", "customer-segments")
        }
        assert len({round(t) for t in tops.values()}) == 1, tops
    finally:
        context.close()
