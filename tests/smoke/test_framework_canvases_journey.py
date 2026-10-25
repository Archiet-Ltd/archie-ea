"""A business architect opens each of the twelve framework canvases from the Canvases page and finds every box
filled from the model, follows every entry to its element, adds an entry through the box's own picker, and finds
the element a second canvas shares with it: the same record, not a copy.
"""

import re

import pytest
from playwright.sync_api import expect

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from tests.smoke.conftest import PASSWORD
from tests.smoke.test_business_model_canvas_journey import _assert_persona, _login

PAGE_TIMEOUT = 30000
expect.set_options(timeout=PAGE_TIMEOUT)

# canvas type -> (the form tag the page marks its drawing with, an element it shares, the canvas type that also shows it)
TYPES = {
    "lean_canvas": ("C4", "Municipal water utilities", "product_vision_board"),
    "empathy_map": ("C4", "Customers wait weeks for recalibration", "swot_analysis"),
    "swot_analysis": ("C2", "Customers wait weeks for recalibration", "lean_canvas"),
    "pestle_analysis": ("C2", "Component prices rising with supply shortages", "porters_five_forces"),
    "porters_five_forces": ("C2", "Municipal water utilities", "lean_canvas"),
    "ansoff_matrix": ("C2", "Field Tester Rollout", "mckinsey_7s"),
    "mckinsey_7s": ("C2", "Win on certified accuracy, not price", "balanced_scorecard"),
    "blue_ocean": ("C2", "Recovered assemblies recertified instead of scrapped", "product_vision_board"),
    "wardley_mapping": ("C1", "Municipal water utilities", "lean_canvas"),
    "balanced_scorecard": ("C2", "Cut calibration turnaround to five days", "okr"),
    "okr": ("C2", "Calibration Automation Phase 1", "balanced_scorecard"),
    "product_vision_board": ("C4", "Recover every sensor assembly for reuse", "balanced_scorecard"),
}

CANVAS_NAMES = {k: CANVAS_TEMPLATES[k]["name"] for k in TYPES}

# The recognisable form of each canvas, asked of the rendered boxes: (box a, relation, box b) in screen terms.
FORMS = {
    "lean_canvas": [("problem", "left-of", "solution"), ("solution", "left-of", "unique-value-proposition"),
                    ("unique-value-proposition", "left-of", "customer-segments"), ("solution", "above", "key-metrics"),
                    ("customer-segments", "above", "revenue-streams")],
    "empathy_map": [("says", "left-of", "user"), ("user", "left-of", "thinks"), ("says", "above", "does"),
                    ("thinks", "above", "feels"), ("does", "above", "pains"), ("pains", "left-of", "gains")],
    "swot_analysis": [("strengths", "left-of", "weaknesses"), ("opportunities", "left-of", "threats"),
                      ("strengths", "above", "opportunities"), ("weaknesses", "above", "threats")],
    "pestle_analysis": [("political", "left-of", "economic"), ("economic", "left-of", "social"),
                        ("political", "above", "technological"), ("technological", "left-of", "legal"),
                        ("legal", "left-of", "environmental")],
    "porters_five_forces": [("new-entrants", "above", "rivalry"), ("rivalry", "above", "substitutes"),
                            ("suppliers", "left-of", "rivalry"), ("rivalry", "left-of", "buyers")],
    "ansoff_matrix": [("market-penetration", "left-of", "product-development"),
                      ("market-development", "left-of", "diversification"),
                      ("market-penetration", "above", "market-development"),
                      ("product-development", "above", "diversification")],
    "mckinsey_7s": [("strategy", "above", "shared-values"), ("shared-values", "above", "staff"),
                    ("skills", "west-of", "shared-values"), ("shared-values", "west-of", "structure"),
                    ("style", "west-of", "shared-values"), ("shared-values", "west-of", "systems"),
                    ("skills", "north-of", "style"), ("structure", "north-of", "systems")],
    "blue_ocean": [("factors", "above", "eliminate"), ("eliminate", "left-of", "raise"),
                   ("reduce", "left-of", "create"), ("eliminate", "above", "reduce")],
    "wardley_mapping": [("user", "above", "genesis"), ("genesis", "left-of", "custom-built"),
                        ("custom-built", "left-of", "product"), ("product", "left-of", "commodity"),
                        ("genesis", "above", "movement"), ("product", "above", "inertia")],
    "balanced_scorecard": [("financial", "above", "vision"), ("internal", "left-of", "vision"),
                           ("vision", "left-of", "customer"), ("vision", "above", "learning"),
                           ("learning", "above", "measures")],
    "okr": [("objective", "above", "key-results"), ("key-results", "left-of", "initiatives")],
    "product_vision_board": [("vision", "above", "target-group"), ("target-group", "left-of", "needs"),
                             ("needs", "left-of", "product"), ("product", "left-of", "business-goals")],
}

# "left-of" and "above" are the same row or column of the drawing; "west-of" and "north-of" only say which side,
# for the corners of the hexagon and the cross.
_RELATIONS = {
    "left-of": "a.right <= b.left + 2 && a.top < b.bottom && b.top < a.bottom",
    "above": "a.bottom <= b.top + 2 && a.left < b.right && b.left < a.right",
    "west-of": "a.right <= b.left + 2",
    "north-of": "a.bottom <= b.top + 2",
}


def _seed(org_id):
    """The Business Model Canvas, the twelve framework canvases, and one spare element per canvas for the picker."""
    from app import create_app, db
    from app.commands import seed_framework_canvases
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service

    app = create_app("testing")
    with app.app_context():
        bmc = BusinessModelCanvas.query.filter_by(organization_id=org_id, canvas_type=None).first()
        if bmc is None:
            bmc = BusinessModelCanvas(name="Journey Business Model", organization_id=org_id)
            db.session.add(bmc)
            db.session.flush()
        _seed_canvas_model(org_id, bmc)
        seed_framework_canvases.seed(org_id)
        ids = {}
        seeded_names = {key: name for key, name, _boxes in seed_framework_canvases.CANVASES}
        for canvas_type in TYPES:
            canvas = BusinessModelCanvas.query.filter_by(
                organization_id=org_id, canvas_type=canvas_type, name=seeded_names[canvas_type]
            ).one()
            ids[canvas_type] = canvas.id
            el_type = CANVAS_TEMPLATES[canvas_type]["zones"][0]["element_types"][0]
            spare = f"Spare {canvas_type} element"
            if not ArchiMateElement.query.filter_by(organization_id=org_id, name=spare).count():
                db.session.add(ArchiMateElement(
                    name=spare, type=el_type, layer=model_service._LAYER_BY_TYPE.get(el_type, "business"),
                    organization_id=org_id,
                ))
        db.session.commit()
        return ids


def _count(name, org_id):
    from app import create_app
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        return ArchiMateElement.query.filter_by(name=name, organization_id=org_id).count()


def _boxes(canvas_type):
    return [z["box_key"].replace("_", "-") for z in CANVAS_TEMPLATES[canvas_type]["zones"] if z["membership"] != "attribute"]


def _hrefs(page, boxes):
    found = {}
    for box in boxes:
        notes = page.locator(f'[data-canvas-notes="{box}"] > li')
        assert notes.count() >= 1, f"{box} is empty on a seeded company"
        for i in range(notes.count()):
            link = notes.nth(i).locator("a[data-note-text]")
            assert link.count() == 1, f"{box} entry {i} does not open anything"
            found[link.get_attribute("href")] = link.inner_text()
    return found


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("canvas_type", [t for t in TYPES if t != "wardley_mapping"])  # the Wardley Map is a plot: test_wardley_map_journey.py
def test_architect_opens_a_framework_canvas_filled_from_the_model(browser, live_server, seeded, canvas_type):
    form, shared, other_type = TYPES[canvas_type]
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    org_id = seeded["ids"]["org"]
    ids = _seed(org_id)
    name = CANVAS_TEMPLATES[canvas_type]["name"]
    boxes = _boxes(canvas_type)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _login(page, live_server, email)

        # From the Canvases page: the canvas is listed with its framework, and its card opens it.
        page.goto(f"{live_server}/business-model/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id(f"bmc-canvas-type-{ids[canvas_type]}")).to_have_text(name)
        if canvas_type != "lean_canvas":
            # an operating model belongs to the business model canvases; no other card says it is missing
            expect(page.get_by_test_id(f"bmc-canvas-card-{ids[canvas_type]}")).not_to_contain_text("operating model")
        page.get_by_test_id(f"bmc-canvas-card-{ids[canvas_type]}").click()
        page.wait_for_url(re.compile(rf"/business-model/{ids[canvas_type]}$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=f"Lantern Quay Systems: {name}", exact=False).first).to_be_visible()

        # Every box has model content, shows its count and question, and every entry opens the element it names.
        hrefs = _hrefs(page, boxes)
        for box in boxes:
            card = page.locator(f'[data-slot=card]:has([data-canvas-notes="{box}"])').first
            if card.locator("[data-canvas-notes]").first.get_attribute("data-canvas-notes") != box:
                continue  # a sub-area of another box: its count sits beside its own heading, not in the band
            shown = card.locator(f'[data-canvas-notes="{box}"] > li').count()
            count = int(card.locator("[data-canvas-band]").first.get_by_text(re.compile(r"^\s*\d+\s*$")).first.inner_text())
            assert count == shown, (box, count, shown)
            assert card.locator("[data-slot=card-description]").first.inner_text().strip(), box
        for href, text in hrefs.items():
            response = context.request.get(live_server + href)
            assert response.status == 200, (href, response.status)
            assert text.strip() in response.text(), f"{href} does not show {text}"

        # The drawing is one named, focusable, sideways-scrolling region, with the list as its accessible alternative.
        region = page.locator("[data-entelim-form]")
        assert region.count() == 1 and region.get_attribute("data-entelim-form") == form
        assert region.get_attribute("role") == "region" and region.get_attribute("tabindex") == "0"
        assert "overflow-x-auto" in region.get_attribute("class").split()
        assert region.get_attribute("aria-label")
        # geometry only through custom properties (a hidden popover may carry display), and the same data as a real table
        assert page.evaluate(
            "() => [...document.querySelector('[data-entelim-form]').querySelectorAll('[style]')].every(e =>"
            " [...e.style].every(p => p.startsWith('--') || p === 'display'))"
        ), "an element in the drawing positions itself with a style other than a custom property"
        assert page.locator('[data-canvas-pane="list"] table[aria-label$="list view"]').count() == 1
        page.get_by_role("button", name="List", exact=True).click()
        rows = page.locator("[data-canvas-rows] tr")
        assert rows.count() >= len(boxes)
        page.get_by_role("button", name="Canvas", exact=True).click()
        expect(page.locator('[data-canvas-pane="canvas"]')).to_be_visible()

        # It keeps its recognisable form: the boxes sit where the framework puts them.
        for a, relation, b in FORMS[canvas_type]:
            ok = page.evaluate(
                "([a, b]) => { const box = k => document.querySelector(`[data-canvas-notes=\"${k}\"], [data-canvas-empty=\"${k}\"]`)"
                ".closest('[data-slot=card]').getBoundingClientRect();"
                f" const A = box(a), B = box(b); return ((a, b) => {_RELATIONS[relation]})(A, B); }}",
                [a, b],
            )
            assert ok, f"{a} is not {relation} {b}"

        # At phone width the boxes keep their layout and the region scrolls sideways; the page itself does not.
        phone = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 800})
        phone_page = phone.new_page()
        try:
            _login(phone_page, live_server, email)
            phone_page.goto(f"{live_server}/business-model/{ids[canvas_type]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            widths = phone_page.evaluate(
                "() => { const r = document.querySelector('[data-entelim-form]');"
                " return [r.scrollWidth, r.clientWidth, document.documentElement.scrollWidth, window.innerWidth]; }"
            )
            assert widths[0] > widths[1], widths
            assert widths[2] <= widths[3] + 1, widths
            for a, relation, b in FORMS[canvas_type][:2]:
                assert phone_page.evaluate(
                    "([a, b]) => { const box = k => document.querySelector(`[data-canvas-notes=\"${k}\"], [data-canvas-empty=\"${k}\"]`)"
                    ".closest('[data-slot=card]').getBoundingClientRect();"
                    f" const A = box(a), B = box(b); return ((a, b) => {_RELATIONS[relation]})(A, B); }}",
                    [a, b],
                ), f"{a} is not {relation} {b} at 390 wide"
        finally:
            phone.close()

        # The shared element is the same record on the second canvas: the same link, the same page.
        shared_href = next(h for h, t in hrefs.items() if t.strip() == shared)
        page.goto(f"{live_server}/business-model/{ids[other_type]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator(f'a[data-note-text][href="{shared_href}"]').first).to_be_visible()
        assert _count(shared, org_id) == 1, "the shared element exists twice"

        # Add an entry through a box's own picker; it survives a reload, and the second canvas offers the same
        # element without retyping it.
        page.goto(f"{live_server}/business-model/{ids[canvas_type]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        first = boxes[0]
        spare = f"Spare {canvas_type} element"
        page.get_by_role("button", name=re.compile(r"^Add to ")).first.click()
        picker = page.get_by_test_id(f"bmc-pick-{first.replace('-', '_')}")
        options = picker.locator("option").all_inner_texts()
        label = next(o for o in options if o.startswith(spare))
        picker.select_option(label=label)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id(f"bmc-add-submit-{first.replace('-', '_')}").click()
        page.reload()
        expect(page.locator(f'[data-canvas-notes="{first}"]')).to_contain_text(spare)
        assert _count(spare, org_id) == 1, "picking an existing element created a second one"

        assert not errors, errors
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_architect_creates_each_framework_from_the_canvases_page_and_sees_why_it_is_empty(browser, live_server, seeded):
    """The Canvases page creates any framework: the new canvas opens empty, every box says what is missing and offers
    the next action, and the canvas is listed with its framework after a reload."""
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        for canvas_type in TYPES:
            name = f"Created {canvas_type}"
            page.goto(f"{live_server}/business-model/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_role("button", name="New Canvas").first.click()
            offered = page.get_by_test_id("bmc-type-select").locator("option").all_inner_texts()
            assert CANVAS_NAMES[canvas_type] in offered and "Business Model Canvas" in offered
            page.get_by_test_id("bmc-type-select").select_option(label=CANVAS_NAMES[canvas_type])
            page.fill("#bmc-name", name)
            with page.expect_navigation(timeout=PAGE_TIMEOUT):
                page.get_by_test_id("bmc-create-submit").click()
            expect(page.get_by_role("heading", name=name).first).to_be_visible()
            assert page.locator(f'[data-canvas="{canvas_type.replace("_", "-")}"]').count() == 1
            if canvas_type == "wardley_mapping":  # a plot has no boxes; its empty state offers the first step
                empty = page.locator('[data-canvas-empty="wardley"]')
                expect(empty).to_contain_text("No map yet")
                assert empty.get_by_role("button", name=re.compile(r"^Add ")).count() == 1
                continue
            for box in _boxes(canvas_type):
                empty = page.locator(f'[data-canvas-empty="{box}"]')
                expect(empty).to_contain_text("No ")
                expect(empty).to_contain_text("yet")
                if box not in ("cost-structure", "revenue-streams"):
                    assert empty.get_by_role("button", name=re.compile(r"^Add to ")).count() == 1, box
            for phrase in ("Nothing here yet", "No data", "N/A"):
                assert phrase not in page.locator("main").inner_text(), (canvas_type, phrase)
            page.reload()
            expect(page.get_by_role("heading", name=name).first).to_be_visible()
        page.goto(f"{live_server}/business-model/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for canvas_type in TYPES:
            card = page.locator("[data-testid^=bmc-canvas-card-]", has_text=f"Created {canvas_type}")
            expect(card).to_contain_text(CANVAS_NAMES[canvas_type])
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_walk_captures_every_canvas_in_both_widths_and_themes(browser, live_server, seeded, tmp_path):
    """Evidence for the design review: each canvas at 1440 and 390 wide, light and dark, with a caption naming the
    elements that fill its boxes. Set SMOKE_SCREENSHOT_DIR to keep the images; otherwise they are written to a
    temporary directory."""
    import os
    import pathlib

    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    ids = _seed(seeded["ids"]["org"])
    out = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    captions = ["# Framework canvases, as the business architect sees them", ""]
    for canvas_type in TYPES:
        for width, height in ((1440, 900), (390, 800)):
            context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
            page = context.new_page()
            try:
                _login(page, live_server, email)
                page.goto(f"{live_server}/business-model/{ids[canvas_type]}", wait_until="networkidle", timeout=PAGE_TIMEOUT)
                if width == 1440:
                    boxes = page.evaluate(
                        "() => [...document.querySelectorAll('[data-canvas-notes]')].map(n => [n.dataset.title,"
                        " [...n.querySelectorAll('[data-note-text]')].map(a => a.textContent.trim())])"
                    )
                    captions.append(f"## {CANVAS_NAMES[canvas_type]}")
                    captions.extend(f"- {title}: {'; '.join(names)}" for title, names in boxes)
                    captions.append("")
                for theme in ("light", "dark"):
                    page.evaluate(f"document.documentElement.classList.toggle('dark', {str(theme == 'dark').lower()})")
                    page.wait_for_timeout(600)  # let the colour transition settle before the picture
                    target = out / f"{canvas_type.replace('_', '-')}-{width}-{theme}.png"
                    page.screenshot(path=str(target), full_page=True)
                    assert target.stat().st_size > 5000, target
            finally:
                context.close()
    for width, height in ((1440, 900), (390, 800)):
        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
        page = context.new_page()
        try:
            _login(page, live_server, email)
            page.goto(f"{live_server}/business-model/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            if width == 1440:
                cards = page.locator("[data-testid^=bmc-canvas-card-]").all_inner_texts()
                captions += ["## Canvases page", f"- {len(cards)} canvases listed, each with its framework", ""]
            for theme in ("light", "dark"):
                page.evaluate(f"document.documentElement.classList.toggle('dark', {str(theme == 'dark').lower()})")
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"canvases-{width}-{theme}.png"), full_page=True)
            page.evaluate("document.documentElement.classList.remove('dark')")
            page.get_by_role("button", name="New Canvas").first.click()
            expect(page.get_by_test_id("bmc-type-select")).to_be_visible()
            page.screenshot(path=str(out / f"canvases-new-{width}-light.png"))
        finally:
            context.close()
    (out / "captions.md").write_text("\n".join(captions) + "\n", encoding="utf8")
    assert len(list(out.glob("*.png"))) == len(TYPES) * 4 + 6
