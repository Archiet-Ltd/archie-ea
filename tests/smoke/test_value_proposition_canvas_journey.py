"""A business architect makes a Value Proposition Canvas for "Municipal water utilities", fills in a job, a pain and
a pain reliever, then opens the Business Model Canvas and finds the same customer segment there: renaming it on
one canvas renames it on the other. The shape keeps its form on a phone, and sign-up offers the canvas.
"""

import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_business_model_canvas_journey import PAGE_TIMEOUT, _assert_persona, _login

expect.set_options(timeout=PAGE_TIMEOUT)

SEGMENT = "Municipal water utilities"
RENAMED = "Regional water boards"
WALK = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or "/tmp/vpc01-walk")


def _seed_model(org_id):
    """A Business Model Canvas that already holds the segment, so the two canvases have something to share."""
    from app import create_app, db
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        bmc = BusinessModelCanvas(name="Water model", organization_id=org_id)
        db.session.add(bmc)
        db.session.flush()
        _seed_canvas_model(org_id, bmc)
        db.session.commit()
        return bmc.id


def _segment_count(org_id, name):
    from app import create_app
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        return ArchiMateElement.query.filter_by(organization_id=org_id, name=name).count()


def _add(page, box_title, box_key, name):
    page.get_by_role("button", name=f"Add to {box_title}").first.click()
    page.get_by_test_id(f"bmc-name-{box_key}").fill(name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_test_id(f"bmc-add-submit-{box_key}").click()


def _notes(page, box):
    return page.locator(f'[data-canvas-notes="{box}"]')


@pytest.mark.smoke
@pytest.mark.journey
def test_architect_builds_a_value_proposition_canvas_that_shares_its_segment(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    org_id = seeded["ids"]["org"]
    bmc_id = _seed_model(org_id)
    assert _segment_count(org_id, SEGMENT) == 1
    WALK.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _login(page, live_server, email)

        # Create the canvas from the Canvases page, choosing the framework by name.
        page.goto(f"{live_server}/business-model/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="New Canvas").first.click()
        assert "Value Proposition Canvas" in page.get_by_test_id("bmc-type-select").locator("option").all_inner_texts()
        page.get_by_test_id("bmc-type-select").select_option(label="Value Proposition Canvas")
        page.fill("#bmc-name", "Water utilities proposition")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("bmc-create-submit").click()
        canvas_url = page.url
        expect(page.locator('[data-canvas="value-proposition-canvas"]')).to_be_visible()
        # Every box says why it is empty and offers the next step.
        for box in ("customer-segments", "customer-jobs", "pains", "pain-relievers"):
            empty = page.locator(f'[data-canvas-empty="{box}"]')
            expect(empty).to_contain_text("No ")
            assert empty.get_by_role("button", name=re.compile(r"^Add to ")).count() == 1, box

        # The customer segment, one job, one pain and one pain reliever; each survives a reload.
        _add(page, "Customer Segment", "customer_segments", SEGMENT)
        _add(page, "Customer Jobs", "customer_jobs", "Report water quality to the regulator")
        _add(page, "Pains", "pains", "Sensors drift between audits")
        _add(page, "Pain Relievers", "pain_relievers", "Drift alerts before the audit")
        page.reload()
        expect(_notes(page, "customer-segments")).to_contain_text(SEGMENT)
        expect(_notes(page, "customer-jobs")).to_contain_text("Report water quality to the regulator")
        expect(_notes(page, "pains")).to_contain_text("Sensors drift between audits")
        expect(_notes(page, "pain-relievers")).to_contain_text("Drift alerts before the audit")
        assert _segment_count(org_id, SEGMENT) == 1, "typing the segment must find the model's element, not make a copy"
        page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / "vpc-1440-light.png"))
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_timeout(600)
        page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / "vpc-1440-dark.png"))
        page.evaluate("document.documentElement.classList.remove('dark')")

        # The value map is the square on the left and the customer profile the circle on the right.
        shapes = page.locator("[data-canvas='value-proposition-canvas'] svg[viewBox='0 0 100 100']").evaluate_all(
            "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })")
        square, circle = shapes[0], shapes[1]
        assert square[0] < circle[0] and abs(square[1] - circle[1]) < 2, shapes
        assert page.locator("[data-canvas='value-proposition-canvas'] svg[viewBox='0 0 100 100'] circle").count() == 1
        assert page.locator("[data-canvas='value-proposition-canvas'] svg[viewBox='0 0 100 100'] rect").count() == 1
        assert "Strategyzer" in page.locator("[data-canvas='value-proposition-canvas']").inner_text()

        # The page names the Business Model Canvas that holds the same segment, and the link opens it.
        shared = page.get_by_test_id("vpc-shared-segments")
        expect(shared).to_contain_text(SEGMENT)
        shared.get_by_role("link", name="Water model").click()
        page.wait_for_url(re.compile(rf"/business-model/{bmc_id}$"), timeout=PAGE_TIMEOUT)
        expect(_notes(page, "customer-segments")).to_contain_text(SEGMENT)
        segment_link = _notes(page, "customer-segments").get_by_role("link", name=SEGMENT)
        href = segment_link.get_attribute("href")
        assert context.request.get(live_server + href).status == 200

        # Rename it here; the Value Proposition Canvas shows the new name, and no second element appeared.
        card = page.locator("[data-canvas-note]", has_text=SEGMENT)
        card.get_by_role("button", name="Edit this entry").click()
        page.locator("[data-note-rename-input]:visible").fill(RENAMED)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.locator("[data-note-rename-save]:visible").click()
        expect(_notes(page, "customer-segments")).to_contain_text(RENAMED)
        page.goto(canvas_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_notes(page, "customer-segments")).to_contain_text(RENAMED)
        expect(_notes(page, "customer-segments")).not_to_contain_text(SEGMENT)
        assert _segment_count(org_id, RENAMED) == 1 and _segment_count(org_id, SEGMENT) == 0

        # And the other way: renaming on the Value Proposition Canvas changes the Business Model Canvas.
        page.locator("[data-canvas-note]", has_text=RENAMED).get_by_role("button", name="Edit this entry").click()
        page.locator("[data-note-rename-input]:visible").fill(SEGMENT)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.locator("[data-note-rename-save]:visible").click()
        page.goto(f"{live_server}/business-model/{bmc_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_notes(page, "customer-segments")).to_contain_text(SEGMENT)
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_a_segment_on_no_business_model_canvas_says_so_and_the_shape_holds_on_a_phone(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    from app import create_app, db
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name="Lonely proposition", organization_id=seeded["ids"]["org"], canvas_type="value_proposition_canvas")
        db.session.add(canvas)
        db.session.flush()
        model_service.add_entry(canvas, "customer_segments", name="Survey firms on a budget")
        canvas_id = canvas.id
    WALK.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("vpc-shared-segments")).to_contain_text("on no other canvas yet")
        region = page.get_by_role("region", name=re.compile("scroll horizontally"))
        assert region.evaluate("e => e.scrollWidth > e.clientWidth"), "the shape must scroll sideways, not stack"
        shapes = page.locator("[data-canvas='value-proposition-canvas'] svg[viewBox='0 0 100 100']").evaluate_all(
            "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top]; })")
        assert shapes[0][0] < shapes[1][0] and abs(shapes[0][1] - shapes[1][1]) < 2, shapes
        page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / "vpc-390-light.png"))
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_timeout(600)
        page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / "vpc-390-dark.png"))
    finally:
        context.close()
