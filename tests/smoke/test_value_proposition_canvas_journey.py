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


BOXES = {
    "customer-segments": "Customer Segment", "value-propositions": "Value Proposition", "products-services": "Products and Services",
    "gain-creators": "Gain Creators", "pain-relievers": "Pain Relievers", "gains": "Gains", "pains": "Pains",
    "customer-jobs": "Customer Jobs",
}

_WHOLE = """(card) => {
  const box = card.getBoundingClientRect();
  const out = [];
  for (const el of card.querySelectorAll('[data-note-text], [data-canvas-empty] *, li[data-canvas-note]')) {
    if (el.scrollWidth > el.clientWidth + 1) out.push('cut sideways: ' + el.textContent.trim().slice(0, 40));
    const r = el.getBoundingClientRect();
    if (r.width && r.right > box.right + 1) out.push('outside its box: ' + el.textContent.trim().slice(0, 40));
  }
  const area = card.querySelector('[data-slot=card-content]');
  if (area && area.getBoundingClientRect().bottom > box.bottom + 1) out.push('scroll area runs past its box');
  if (card.scrollHeight > card.clientHeight + 1) out.push('box content taller than the box');
  return out;
}"""

_SHAPES = """() => {
  const svgs = [...document.querySelectorAll("[data-canvas='value-proposition-canvas'] svg[viewBox='0 0 100 100']")];
  return svgs.map((svg) => {
    const r = svg.getBoundingClientRect();
    return {width: r.width, height: r.height, left: r.left, top: r.top, radius: getComputedStyle(svg.parentElement).borderRadius};
  });
}"""


def _assert_shape(page, where):
    """The value map stays a square and the customer profile a circle, whatever the entries, and nothing crosses the circle."""
    square, circle = page.evaluate(_SHAPES)
    assert abs(square["width"] - square["height"]) <= 0.02 * square["width"], (where, "value map is not square", square)
    assert abs(circle["width"] - circle["height"]) <= 1, (where, "customer profile is not round", circle)
    assert circle["radius"] == "50%", (where, "customer profile is not drawn as a circle", circle["radius"])
    assert square["left"] < circle["left"] and abs(square["top"] - circle["top"]) < 2, (where, square, circle)
    # Every box of the profile sits inside the circle, corners included.
    outside = page.evaluate(
        """() => {
          const svg = document.querySelectorAll("[data-canvas='value-proposition-canvas'] svg[viewBox='0 0 100 100']")[1];
          const c = svg.getBoundingClientRect(), cx = c.left + c.width / 2, cy = c.top + c.height / 2, rad = c.width / 2;
          const bad = [];
          for (const card of svg.parentElement.querySelectorAll('[data-slot=card]')) {
            const r = card.getBoundingClientRect();
            for (const [x, y] of [[r.left, r.top], [r.right, r.top], [r.left, r.bottom], [r.right, r.bottom]]) {
              if (Math.hypot(x - cx, y - cy) > rad + 1) bad.push(card.textContent.trim().slice(0, 20));
            }
          }
          return bad;
        }""")
    assert outside == [], (where, "boxes cross the circle's edge", outside)


def _assert_boxes_whole(page, where):
    """Every box shows each entry in full and keeps its add control in the window and inside the box."""
    for key, title in BOXES.items():
        card = page.locator(f'[data-slot=card]:has([data-canvas-notes="{key}"], [data-canvas-empty="{key}"])').last
        card.scroll_into_view_if_needed()
        assert card.evaluate(_WHOLE) == [], (where, key, card.evaluate(_WHOLE))
        adds = card.get_by_role("button", name=f"Add to {title}")
        assert adds.count() >= 1, (where, key, "no add control")
        for i in range(adds.count()):
            add = adds.nth(i)
            add.scroll_into_view_if_needed()
            inside = add.evaluate(
                """(el, card) => { const r = el.getBoundingClientRect(), b = card.getBoundingClientRect();
                  return r.width > 0 && r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth
                    && r.top >= b.top - 1 && r.bottom <= b.bottom + 1 && r.left >= b.left - 1 && r.right <= b.right + 1; }""",
                card.element_handle())
            assert inside, (where, key, "add control not whole in the window and its box", add.bounding_box())


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


FIVE = {
    "customer_segments": "customer segment", "value_propositions": "value proposition", "products_services": "product or service",
    "gain_creators": "gain creator", "pain_relievers": "pain reliever", "gains": "gain", "pains": "pain", "customer_jobs": "customer job",
}


def _fill_to_five(canvas_id):
    """Top every box up to five long entries, so wrapping and scrolling inside each box are exercised."""
    from app import create_app
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas.query.get(canvas_id)
        for key, label in FIVE.items():
            have = len(model_service.entries(canvas, key))
            for n in range(have + 1, 6):
                model_service.add_entry(canvas, key, name=f"Water board {label} number {n}: a long entry that has to wrap in full inside its box")


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
        _assert_boxes_whole(page, "empty, 1440")
        _assert_shape(page, "empty, 1440")

        # The customer segment, one job, one pain and one pain reliever; each survives a reload.
        _add(page, "Customer Segment", "customer_segments", SEGMENT)
        _add(page, "Customer Jobs", "customer_jobs", "Report water quality to the regulator")
        _add(page, "Pains", "pains", "Sensors drift between audits")
        _add(page, "Pain Relievers", "pain_relievers", "Drift alerts before the audit")
        _add(page, "Gains", "gains", "Reach every household with a reading the regulator accepts without a second audit")
        _add(page, "Gain Creators", "gain_creators", "Continuous sampling that replaces the quarterly manual round")
        page.reload()
        expect(_notes(page, "customer-segments")).to_contain_text(SEGMENT)
        expect(_notes(page, "customer-jobs")).to_contain_text("Report water quality to the regulator")
        expect(_notes(page, "pains")).to_contain_text("Sensors drift between audits")
        expect(_notes(page, "pain-relievers")).to_contain_text("Drift alerts before the audit")
        assert _segment_count(org_id, SEGMENT) == 1, "typing the segment must find the model's element, not make a copy"
        _assert_boxes_whole(page, "filled, 1440")
        _assert_shape(page, "filled, 1440")
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        _assert_boxes_whole(page, "filled, 390")
        _assert_shape(page, "filled, 390")
        _fill_to_five(int(canvas_url.rsplit("/", 1)[1]))
        page.reload()
        _assert_boxes_whole(page, "five entries, 390")
        _assert_shape(page, "five entries, 390")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.reload()
        _assert_boxes_whole(page, "five entries, 1440")
        _assert_shape(page, "five entries, 1440")
        for key, label in FIVE.items():
            assert _notes(page, key.replace("_", "-")).locator("[data-canvas-note]").count() == 5, key
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
        _assert_boxes_whole(page, "one segment, 390")
        _assert_shape(page, "one segment, 390")
        page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / "vpc-390-light.png"))
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_timeout(600)
        page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / "vpc-390-dark.png"))
    finally:
        context.close()


_ADD_WHOLE = """(add) => {
  const card = add.closest('[data-slot=card]');
  const area = card.querySelector('[data-slot=card-content]');
  const a = add.getBoundingClientRect(), c = card.getBoundingClientRect(), s = area.getBoundingClientRect();
  return {add: [a.left, a.top, a.right, a.bottom], card: [c.left, c.top, c.right, c.bottom], area: [s.left, s.top, s.right, s.bottom],
    inside: a.width > 0 && a.top >= s.top - 1 && a.bottom <= s.bottom + 1 && a.left >= c.left - 1 && a.right <= c.right + 1
      && a.bottom <= c.bottom + 1};
}"""


def _assert_empty_adds_whole(page, where, empty_keys):
    """For each empty box, the add button inside the empty state lies wholly inside the box as drawn, with no scrolling of the box to find it."""
    for key in empty_keys:
        empty = page.locator(f'[data-canvas-empty="{key}"]')
        assert empty.count() == 1, (where, key, "box is not empty")
        add = empty.get_by_role("button", name=re.compile(r"^Add to "))
        assert add.count() == 1, (where, key, "no add button in the empty box")
        measured = add.evaluate(_ADD_WHOLE)
        assert measured["inside"], (where, key, "add button is cut by its box", measured)


def _new_canvas(org_id, name, segment=True):
    from app import create_app, db
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name=name, organization_id=org_id, canvas_type="value_proposition_canvas")
        db.session.add(canvas)
        db.session.flush()
        if segment:
            model_service.add_entry(canvas, "customer_segments", name="Municipal water utilities")
            model_service.add_entry(canvas, "customer_jobs", name="Report water quality")
            model_service.add_entry(canvas, "pains", name="Sensors drift")
            model_service.add_entry(canvas, "pain_relievers", name="Drift alerts")
        db.session.commit()
        return canvas.id


@pytest.mark.smoke
@pytest.mark.journey
def test_every_empty_box_shows_its_whole_add_button_at_every_width(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    org_id = seeded["ids"]["org"]
    fresh = _new_canvas(org_id, "Fresh proposition", segment=False)
    partial = _new_canvas(org_id, "Partial proposition", segment=True)
    WALK.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        every = list(BOXES)
        for canvas_id, empty_keys, width, dark, label in [
            (fresh, every, 1440, False, "fresh-1440-light"),
            (fresh, every, 390, True, "fresh-390-dark"),
            (partial, ["products-services", "gain-creators", "gains", "value-propositions"], 1440, False, "partial-1440-light"),
            (partial, ["products-services", "gain-creators", "gains", "value-propositions"], 390, True, "partial-390-dark"),
        ]:
            page.set_viewport_size({"width": width, "height": 900 if width > 400 else 844})
            page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
            page.wait_for_timeout(400)
            _assert_empty_adds_whole(page, label, empty_keys)
            if label.startswith(("fresh-1440", "fresh-390")):
                page.locator("[data-canvas='value-proposition-canvas']").screenshot(path=str(WALK / f"vpc04-empty-{label}.png"))
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_a_second_value_proposition_is_added_beside_the_first(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    canvas_id = _new_canvas(seeded["ids"]["org"], "Two propositions", segment=True)
    first = "Continuous sampling that replaces the quarterly manual round and reaches every household with a reading the regulator accepts"
    second = "A drift alert that reaches the duty engineer before the audit"
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _add(page, "Value Proposition", "value_propositions", first)
        _add(page, "Value Proposition", "value_propositions", second)
        page.reload()
        notes = _notes(page, "value-propositions")
        expect(notes.locator("[data-canvas-note]")).to_have_count(2)
        expect(notes).to_contain_text(first)
        expect(notes).to_contain_text(second)
    finally:
        context.close()
