"""A founder reads the business model canvas and the lean canvas: a block that holds more entries than its height
shows only whole entries, a plain line under them says how many more there are, and activating it shows the rest.
Nothing is ever cut through its text.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_canvas_fits_screen_and_credit import CASES, PAGE_TIMEOUT, _login, _seed

expect.set_options(timeout=PAGE_TIMEOUT)

MEASURE = """() => [...document.querySelectorAll('[data-canvas-scroll]')].map(box => {
  const frame = box.getBoundingClientRect();
  const notes = [...box.querySelectorAll('[data-canvas-note]')];
  const shown = notes.filter(n => n.getClientRects().length);
  const cut = shown.filter(n => { const r = n.getBoundingClientRect(); return r.bottom > frame.bottom + 1 || r.top < frame.top - 1; });
  const more = box.querySelector('[data-canvas-more]');
  return {
    title: box.getAttribute('aria-label'),
    total: notes.length,
    shown: shown.length,
    cut: cut.map(n => n.innerText.trim()),
    more: more && more.getClientRects().length ? more.innerText.trim() : null,
    expanded: more ? more.getAttribute('aria-expanded') : null,
  };
})"""


def _blocks(page):
    return page.evaluate(MEASURE)


def _check(page, label):
    failures = []
    overflowing = 0
    for b in _blocks(page):
        if b["cut"]:
            failures.append(f"{label}: {b['title']} cuts an entry through its text: {b['cut']}")
        hidden = b["total"] - b["shown"]
        if hidden:
            overflowing += 1
            match = re.fullmatch(r"(\d+) more", b["more"] or "")
            if not match or int(match.group(1)) != hidden:
                failures.append(f"{label}: {b['title']} hides {hidden} entries but its line reads {b['more']!r}")
        elif b["more"] and b["expanded"] != "true":
            failures.append(f"{label}: {b['title']} shows every entry yet offers {b['more']!r}")
    if not overflowing:
        failures.append(f"{label}: no block holds more entries than fit, the seeded data should have at least one")
    return failures


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("canvas_type", list(CASES))
@pytest.mark.parametrize("width,height,theme", [(1440, 900, "light"), (390, 844, "dark")])
def test_founder_reads_whole_entries_and_opens_the_rest(browser, live_server, seeded, canvas_type, width, height, theme):
    ids = _seed(seeded["ids"]["org"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    script = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
    if theme == "dark":
        script += " document.documentElement.classList.add('dark');"
    context.add_init_script(script)
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(f"{live_server}/business-model/{ids[canvas_type]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Revenue Streams", exact=True).first).to_be_attached()
        label = f"{canvas_type or 'bmc'} {width} {theme}"
        failures = _check(page, label)
        assert not failures, failures

        # Activating the line by keyboard shows the rest of that block, and only that block.
        more = page.locator("[data-canvas-more]:visible").first
        box = more.locator("xpath=ancestor::*[@data-canvas-scroll][1]")
        total = box.locator("[data-canvas-note]").count()
        more.focus()
        page.keyboard.press("Enter")
        expect(more).to_have_attribute("aria-expanded", "true")
        shown = box.locator("[data-canvas-note]:visible").count()
        assert shown == total, f"{label}: {shown} of {total} entries visible after opening the block"
    finally:
        context.close()
