"""A founder opens the business model canvas and the lean canvas on a laptop: the whole canvas, including the
bottom strip, is on the first screen, the licence credit the published canvas requires is on the page and in the
downloaded file, and on a phone the nine blocks are still a canvas that scrolls sideways.
"""

import io
import json
import zipfile

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
expect.set_options(timeout=PAGE_TIMEOUT)

BMC_CREDIT = "Business Model Canvas by Strategyzer AG (strategyzer.com), Alexander Osterwalder and Yves Pigneur, licensed under CC BY-SA."
LEAN_CREDIT = "Lean Canvas adapted by Ash Maurya from the Business Model Canvas, CC BY-SA."

# framework -> (the blocks of the bottom strip, the credit sentences the page and export must carry)
CASES = {
    None: (("cost-structure", "revenue-streams"), (BMC_CREDIT,)),
    "lean_canvas": (("cost-structure", "revenue-streams"), (BMC_CREDIT, LEAN_CREDIT)),
}
EXPORT_FORMATS = ("mermaid", "lucid", "archi")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _seed(org_id):
    from app import create_app, db
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        ids = {}
        for canvas_type in CASES:
            canvas = BusinessModelCanvas(name=f"Fit {canvas_type or 'bmc'}", organization_id=org_id, canvas_type=canvas_type)
            db.session.add(canvas)
            db.session.flush()
            _seed_canvas_model(org_id, canvas)
            ids[canvas_type] = canvas.id
        db.session.commit()
        return ids


def _unreachable_note_actions(page):
    """Every note's edit and remove actions take focus from the keyboard and show when they do."""
    failures = []
    notes = page.locator("[data-canvas-pane=canvas] [data-canvas-note]")
    assert notes.count() > 0, "no notes to check"
    for i in range(min(notes.count(), 6)):
        note = notes.nth(i)
        buttons = note.locator("button[data-note-edit], button[data-note-remove]")
        for k in range(buttons.count()):
            button = buttons.nth(k)
            if not button.get_attribute("aria-label"):
                failures.append(f"note {i} action {k} has no accessible label")
            button.focus()
            if not button.evaluate("e => e === document.activeElement"):
                failures.append(f"note {i} action {k} cannot take keyboard focus")
            else:
                try:
                    button.evaluate_handle("e => e.closest('span.absolute')").evaluate(
                        "e => new Promise((ok, no) => { const t = Date.now(); (function w() { getComputedStyle(e).opacity === '1' ? ok() : Date.now() - t > 3000 ? no('slow') : setTimeout(w, 50); })(); })")
                except Exception:
                    opacity = button.evaluate("e => getComputedStyle(e.closest('span.absolute')).opacity")
                    failures.append(f"note {i} action {k} stays invisible while focused (opacity {opacity})")
        edit = note.locator("button[data-note-edit]")
        if edit.count():
            edit.first.focus()
            page.keyboard.press("Enter")
            try:
                expect(note.get_by_role("dialog").first).to_be_visible()
            except AssertionError:
                failures.append(f"note {i}: Enter on the edit action does not open the editor")
            page.keyboard.press("Escape")
    return failures


def _fold_failures(browser, live_server, seeded, canvas_type):
    strip, _ = CASES[canvas_type]
    ids = _seed(seeded["ids"]["org"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(f"{live_server}/business-model/{ids[canvas_type]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Revenue Streams", exact=True).first).to_be_attached()
        failures = []
        for box in strip:
            card = page.locator('[data-slot="card"]').filter(has=page.get_by_text(box.replace("-", " ").title(), exact=True)).first
            bottom = card.locator("[data-canvas-band]").evaluate("e => e.getBoundingClientRect().bottom")
            if bottom > 900:
                failures.append(f"{box} title band ends at {bottom}px, below the 900px first screen")
        failures += _unreachable_note_actions(page)
        return failures
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("canvas_type", list(CASES))
def test_founder_sees_the_bottom_strip_on_the_first_screen(browser, live_server, seeded, canvas_type):
    failures = _fold_failures(browser, live_server, seeded, canvas_type)
    assert not failures, failures


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("canvas_type", list(CASES))
def test_founder_sees_the_credit_and_the_canvas_shape(browser, live_server, seeded, canvas_type):
    strip, credits = CASES[canvas_type]
    ids = _seed(seeded["ids"]["org"])
    url = f"{live_server}/business-model/{ids[canvas_type]}"
    page = None
    for width, height in ((1440, 900), (390, 844)):
        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
        page = context.new_page()
        try:
            _login(page, live_server, seeded["emails"]["business_architect"])
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_text("Revenue Streams", exact=True).first).to_be_attached()
            if width == 390:
                grid = page.locator('[data-entelim-form]').first
                assert grid.evaluate("e => { let n = e; while (n) { if (n.scrollWidth > n.clientWidth + 1 && getComputedStyle(n).overflowX !== 'visible') return true; n = n.parentElement; } return false; }"), \
                    "the nine blocks no longer scroll sideways on a phone"
            body = page.locator("main, body").first.inner_text()
            for credit in credits:
                assert credit in body, f"credit missing on the page at {width}px: {credit}"
        finally:
            context.close()
    context = browser.new_context(ignore_https_errors=True)
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["business_architect"])
        for fmt in EXPORT_FORMATS:
            response = context.request.get(f"{live_server}/business-model/{ids[canvas_type]}/export?format={fmt}")
            assert response.status == 200, (fmt, response.status)
            body_bytes = response.body()
            if fmt == "lucid":
                with zipfile.ZipFile(io.BytesIO(body_bytes)) as archive:
                    body_bytes = archive.read("document.json")
            text = body_bytes.decode("utf-8")
            if fmt == "lucid":
                text = json.loads(text)["credit"] and " ".join(json.loads(text)["credit"])
            for credit in credits:
                assert credit in text, f"credit missing in the {fmt} export: {credit}"
    finally:
        context.close()
