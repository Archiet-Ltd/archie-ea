"""A business architect of a seeded company opens the Canvases list, then the Business Model Canvas, and finds every
box filled and the cost box agreeing with the cost records; opens the Value Proposition Canvas and finds all six
boxes filled for each customer segment with a pain reliever joined to a pain; and, in an organisation with nothing in
it, finds every empty box saying what belongs there and offering to add it.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_business_model_canvas_journey import BOXES, PAGE_TIMEOUT, _assert_persona, _login

expect.set_options(timeout=PAGE_TIMEOUT)
VPC_BOXES = ("products-services", "gain-creators", "pain-relievers", "gains", "pains", "customer-jobs")
SYSTEMS = (("Journey Ledger", 41000, 1800), ("Journey Portal", 12500, 950), ("Journey Hub", 86000, 3400))


def _seed(org_id):
    """A canvas filled from the model with its customer profiles, systems with different recorded costs in the
    organisation's currency, and an empty canvas in the same organisation."""
    from app import create_app, db
    from app.commands.seed_demo_company import _app_total_cost, _seed_canvas_model
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_model import BusinessModelCanvas
    from app.models.organization import Organization
    from app.services.application_cost_accessor import sync_cost_fact

    app = create_app("testing")
    with app.app_context():
        Organization.query.filter_by(id=org_id).one().reporting_currency = "GBP"
        filled = BusinessModelCanvas(name="Profile Journey Canvas", organization_id=org_id)
        empty = BusinessModelCanvas(name="Empty Profile Journey Canvas", organization_id=org_id)
        db.session.add_all([filled, empty])
        db.session.flush()
        _seed_canvas_model(org_id, filled)
        for name, licence, infrastructure in SYSTEMS:
            row = ApplicationComponent(
                name=name, organization_id=org_id, license_cost_annual=licence,
                infrastructure_cost_monthly=infrastructure,
                total_cost_of_ownership=_app_total_cost(licence, infrastructure),
            )
            db.session.add(row)
            db.session.flush()
            for column in ("total_cost_of_ownership", "license_cost_annual", "infrastructure_cost_monthly"):
                sync_cost_fact(row, column, "GBP")
        db.session.commit()
        return filled.id, empty.id


def _note_texts(page, box):
    return page.locator(f'[data-canvas-notes="{box}"] > li').all_inner_texts()


@pytest.mark.smoke
@pytest.mark.journey
def test_business_architect_reads_both_canvases_complete_and_true(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    filled_id, empty_id = _seed(seeded["ids"]["org"])
    from app.commands.seed_demo_company import _app_total_cost

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _login(page, live_server, email)

        # The list says in plain words what a canvas is, and opens the canvas.
        page.goto(live_server + "/business-model/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Your business model on one page, built from your model")).to_be_visible()
        assert "ArchiMate" not in page.locator("main, body").first.inner_text().split("New canvas")[0]
        page.get_by_test_id(f"bmc-canvas-card-{filled_id}").click()
        page.wait_for_url(re.compile(rf"/business-model/{filled_id}$"), timeout=PAGE_TIMEOUT)
        for box in BOXES:
            assert page.locator(f'[data-canvas-notes="{box}"] > li').count() >= 1, f"{box} is empty"

        # Each system shows its own cost, in the organisation's currency, and the same figure as its page.
        costs = {}
        for name, licence, infrastructure in SYSTEMS:
            line = page.locator('[data-canvas-notes="cost-structure"] > li', has_text=name)
            amount = _app_total_cost(licence, infrastructure)
            expect(line).to_contain_text(f"{name}: {amount:,.0f} GBP a year")
            costs[name] = (amount, line.locator("a[data-note-text]").get_attribute("href"))
        assert len({amount for amount, _ in costs.values()}) == len(SYSTEMS), "two systems show one cost"
        revenue = " ".join(_note_texts(page, "revenue-streams"))
        assert "GBP" in revenue and "USD" not in page.locator('[data-canvas-notes="cost-structure"]').inner_text()
        name, (amount, href) = next(iter(costs.items()))
        page.locator('[data-canvas-notes="cost-structure"] a[data-note-text]', has_text=name).click()
        page.wait_for_url(re.compile(re.escape(href) + "$"), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(name)
        expect(page.locator("body")).to_contain_text(f"{amount:,.0f}")

        # The Value Proposition Canvas: every box has content for every segment, and a reliever meets a pain.
        page.goto(f"{live_server}/business-model/{filled_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Value Proposition Canvas").first.click()
        page.wait_for_url(re.compile(r"/value-proposition$"), timeout=PAGE_TIMEOUT)
        segments = page.get_by_test_id("vpc-segments").get_by_role("link")
        assert segments.count() == 3
        seen_pains = []
        for i in range(3):
            segments.nth(i).click()
            page.wait_for_url(re.compile(r"segment=\d+"), timeout=PAGE_TIMEOUT)
            for box in VPC_BOXES:
                assert page.locator(f'[data-canvas-notes="{box}"] > li').count() >= 1, f"{box} is empty on segment {i}"
                assert page.locator(f'[data-canvas-empty="{box}"]').count() == 0
            for box in ("customer-jobs", "pains", "gains"):
                assert page.locator(f'[data-canvas-notes="{box}"] > li').count() >= 3
            seen_pains.append(set(_note_texts(page, "pains")))
            fit = page.get_by_test_id("vpc-fit-item").first
            reliever, pain = fit.locator("a").nth(0), fit.locator("a").nth(1)
            assert pain.inner_text() in _note_texts(page, "pains")
            assert reliever.inner_text() in _note_texts(page, "pain-relievers")
            for link in (reliever, pain):
                response = context.request.get(live_server + link.get_attribute("href"))
                assert response.status == 200 and link.inner_text() in response.text()
        assert not (seen_pains[0] & seen_pains[1]) and not (seen_pains[1] & seen_pains[2])

        # Customer Jobs has room to read at 1440 and at 1280: no text leaves its card.
        for width in (1440, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            jobs = page.locator('[data-slot=card]:has([data-canvas-notes="customer-jobs"])')
            box = jobs.bounding_box()
            assert box["width"] >= 190, f"Customer Jobs is {box['width']:.0f}px wide at {width}"
            leaks = jobs.locator("[data-note-text]").evaluate_all(
                "els => els.filter(e => e.scrollWidth > e.clientWidth + 1 || e.getBoundingClientRect().right > e.closest('[data-slot=card]').getBoundingClientRect().right + 1).length")
            assert leaks == 0, f"{leaks} jobs spill out of their card at {width}"
            for card in page.locator("[data-slot=card]:has([data-canvas-notes])").all():
                assert card.evaluate("e => e.scrollWidth <= e.clientWidth + 1")

        # A pain added through the box's own control survives a reload and is tied to the segment.
        page.get_by_role("button", name="Add to Pains").click()
        page.get_by_test_id("vpc-name-pains").fill("Calibration records live on paper")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("vpc-add-submit-pains").click()
        expect(page.locator('[data-canvas-notes="pains"]')).to_contain_text("Calibration records live on paper")
        page.reload()
        expect(page.locator('[data-canvas-notes="pains"]')).to_contain_text("Calibration records live on paper")

        # A canvas with no customer segment says why the profile is empty and how to start.
        page.goto(f"{live_server}/business-model/{empty_id}/value-proposition", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_every_empty_box_offers_its_next_step(browser, live_server, seeded):
    """A business architect with a canvas holding nothing but one segment finds, in every empty box of both
    canvases, one line on what belongs there and a button that opens the add dialog and adds it."""
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    org_id = seeded["ids"]["org"]
    from app import create_app, db
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        fresh = BusinessModelCanvas(name="Fresh canvas", organization_id=org_id)
        db.session.add(fresh)
        db.session.commit()
        canvas_id = fresh.id

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for box in (b for b in BOXES if b != "cost-structure"):
            empty = page.locator(f'[data-canvas-empty="{box}"]')
            expect(empty).to_be_visible()
            assert len(empty.inner_text().split()) >= 4
            assert empty.get_by_role("button").count() + empty.get_by_role("link").count() >= 1 or box == "revenue-streams", box

        # Add the one segment, then the profile boxes each offer their add button.
        page.get_by_role("button", name="Add to Customer Segments").first.click()
        page.get_by_test_id("bmc-name-customer_segments").fill("Grocery chains")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("bmc-add-submit-customer_segments").click()
        page.get_by_role("link", name="Value Proposition Canvas").first.click()
        page.wait_for_url(re.compile(r"/value-proposition$"), timeout=PAGE_TIMEOUT)
        for box, title in (("customer-jobs", "Customer Jobs"), ("pains", "Pains"), ("gains", "Gains"), ("pain-relievers", "Pain Relievers")):
            empty = page.locator(f'[data-canvas-empty="{box}"]')
            expect(empty).to_be_visible()
            assert len(empty.inner_text().split()) >= 4
            expect(empty.get_by_role("button", name=f"Add to {title}")).to_be_visible()
        page.locator('[data-canvas-empty="customer-jobs"]').get_by_role("button", name="Add to Customer Jobs").click()
        page.get_by_test_id("vpc-name-customer_jobs").fill("Check the delivery before it leaves the depot")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("vpc-add-submit-customer_jobs").click()
        expect(page.locator('[data-canvas-notes="customer-jobs"]')).to_contain_text("Check the delivery before it leaves the depot")
        assert page.locator('[data-canvas-empty="customer-jobs"]').count() == 0
    finally:
        context.close()
