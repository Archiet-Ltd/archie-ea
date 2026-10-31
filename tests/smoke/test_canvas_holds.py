"""A founder works the canvases the way a person does.

On an Empathy Map a person adds a pain and a gain without being asked for a customer segment (that question belongs to
the Value Proposition Canvas alone). On a business model canvas that has nothing in it, every box offers a whole
"Add to ..." button, and the Value Propositions block stands as tall as the boxes around it, as on the published canvas.
"""

import pytest
from playwright.sync_api import expect

from tests.smoke.test_canvas_fits_screen_and_credit import PAGE_TIMEOUT, _login, _seed

expect.set_options(timeout=PAGE_TIMEOUT)


def _context(browser, width, height, theme="light"):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    script = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    if theme == "dark":
        script += " document.addEventListener('DOMContentLoaded', () => document.documentElement.classList.add('dark'));"
    context.add_init_script(script)
    return context


def _canvas(org_id, canvas_type, filled):
    from app import create_app, db
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name=f"Hold {canvas_type or 'bmc'} {'filled' if filled else 'empty'}", organization_id=org_id, canvas_type=canvas_type)
        db.session.add(canvas)
        db.session.flush()
        if filled:
            _seed_canvas_model(org_id, canvas)
        db.session.commit()
        return canvas.id


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("box,label,entry", [("pains", "Pains", "Waiting weeks for recalibration"), ("gains", "Gains", "A calibrated line on day one")])
def test_architect_adds_a_pain_and_a_gain_to_an_empathy_map(browser, live_server, seeded, box, label, entry):
    canvas_id = _canvas(seeded["ids"]["org"], "empathy_map", filled=True)
    context = _context(browser, 1440, 900)
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["business_architect"])
        url = f"{live_server}/business-model/{canvas_id}"
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=f"Add to {label}", exact=True).first.click()
        page.get_by_test_id(f"bmc-name-{box}").fill(entry)
        page.get_by_test_id(f"bmc-add-submit-{box}").click()
        page.wait_for_load_state("domcontentloaded")
        body = page.locator("body").inner_text()
        assert "Choose the customer segment" not in body, "the empathy map asked for a customer segment"
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        notes = page.locator(f'[data-canvas-notes="{box}"]')
        expect(notes).to_contain_text(entry)
    finally:
        context.close()


BOXES = """() => [...document.querySelectorAll('[data-canvas-empty]')].map(e => {
  const button = e.querySelector('button, a');
  if (!button) return null;
  const r = button.getBoundingClientRect();
  const clip = [];
  for (let p = button.parentElement; p && p !== document.body; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (p.hasAttribute('data-canvas-scroll') && /(hidden|auto|scroll|clip)/.test(s.overflowY)) { const c = p.getBoundingClientRect(); clip.push({top: c.top, bottom: c.bottom}); }
  }
  return {box: e.getAttribute('data-canvas-empty'), text: button.innerText.trim(), top: r.top, bottom: r.bottom, height: r.height, scroll: button.scrollHeight, clip};
}).filter(Boolean)"""


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("width,height,theme", [(1440, 900, "light"), (390, 844, "dark")])
def test_empty_business_model_canvas_offers_whole_add_buttons(browser, live_server, seeded, width, height, theme):
    canvas_id = _canvas(seeded["ids"]["org"], None, filled=False)
    context = _context(browser, width, height, theme)
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Revenue Streams", exact=True).first).to_be_attached()
        page.wait_for_timeout(800)
        buttons = page.evaluate(BOXES)
        assert len(buttons) >= 7, buttons
        cut = []
        for b in buttons:
            if not b["text"].startswith("Add to"):
                continue
            for c in b["clip"]:
                if b["top"] < c["top"] - 1 or b["bottom"] > c["bottom"] + 1:
                    cut.append(f"{b['box']}: {b['text']!r} spans {b['top']:.0f}-{b['bottom']:.0f} inside {c['top']:.0f}-{c['bottom']:.0f}")
        assert not cut, cut
    finally:
        context.close()


CENTRE = """() => {
  const card = key => document.getElementById('box-' + key).nextElementSibling.getBoundingClientRect();
  const vp = card('value-propositions');
  const around = ['key-activities', 'key-resources', 'customer-relationships', 'channels'].map(k => card(k));
  return {vp: [vp.top, vp.bottom], around: around.map(r => [r.top, r.bottom])};
}"""


@pytest.mark.smoke
@pytest.mark.journey
def test_value_propositions_block_stands_as_tall_as_its_neighbours(browser, live_server, seeded):
    canvas_id = _canvas(seeded["ids"]["org"], None, filled=True)
    context = _context(browser, 1440, 900)
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Revenue Streams", exact=True).first).to_be_attached()
        page.wait_for_timeout(800)
        m = page.evaluate(CENTRE)
        top = min(t for t, _ in m["around"])
        bottom = max(b for _, b in m["around"])
        assert abs(m["vp"][0] - top) <= 2 and abs(m["vp"][1] - bottom) <= 2, m
    finally:
        context.close()
