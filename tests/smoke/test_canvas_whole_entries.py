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
  const place = n => { const r = n.getBoundingClientRect();
    return r.top >= frame.top - 1 && r.bottom <= frame.bottom + 1 ? 'in' : (r.top >= frame.bottom - 1 || r.bottom <= frame.top + 1 ? 'out' : 'cut'); };
  const state = notes.map(place);
  const count = box.parentElement.querySelector('[data-canvas-count]');
  const toggle = box.parentElement.querySelector('[data-canvas-toggle]');
  return {
    title: box.getAttribute('aria-label'),
    total: notes.length,
    shown: state.filter(s => s === 'in').length,
    cut: notes.filter((n, i) => state[i] === 'cut').map(n => n.innerText.trim()),
    more: count && count.getClientRects().length ? count.innerText.trim() : null,
    toggle: toggle && toggle.getClientRects().length ? toggle.innerText.trim() : null,
    expanded: toggle ? toggle.getAttribute('aria-expanded') : null,
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
        hidden = b["total"] - b["shown"] - len(b["cut"])
        if hidden:
            overflowing += 1
            match = re.fullmatch(r"(\d+) more", b["more"] or "")
            if not match or int(match.group(1)) != hidden:
                failures.append(f"{label}: {b['title']} hides {hidden} entries but its line reads {b['more']!r}")
            if b["toggle"] != "Show all" or b["expanded"] != "false":
                failures.append(f"{label}: {b['title']} hides {hidden} entries but offers {b['toggle']!r} (expanded {b['expanded']})")
        elif b["more"]:
            failures.append(f"{label}: {b['title']} shows every entry yet counts {b['more']!r} more")
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

        # Activating the button by keyboard shows the rest of that block, and only that block.
        toggle = page.locator("[data-canvas-toggle]:visible").first
        box = toggle.locator("xpath=../preceding-sibling::*[@data-canvas-scroll]")
        total = box.locator("[data-canvas-note]").count()
        toggle.focus()
        page.keyboard.press("Enter")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        assert box.evaluate("(b) => [...b.querySelectorAll('[data-canvas-note]')].every(n => { const r = n.getBoundingClientRect(), f = b.getBoundingClientRect(); return r.top >= f.top - 1 && r.bottom <= f.bottom + 1; })"), \
            f"{label}: not every one of the {total} entries lies inside its block after opening it"
        assert not _check_blocks_cut(page), f"{label}: opening one block cut an entry in another"

        # Reaching an entry below the cut from the keyboard opens its block instead of scrolling it half into view.
        page.goto(f"{live_server}/business-model/{ids[canvas_type]}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Revenue Streams", exact=True).first).to_be_attached()
        page.wait_for_timeout(1500)
        reached = page.evaluate("""() => {
          const box = [...document.querySelectorAll('[data-canvas-scroll]')].find(b => b.parentElement.querySelector('[data-canvas-toggle]') && b.parentElement.querySelector('[data-canvas-count]') && b.parentElement.querySelector('[data-canvas-count]').getClientRects().length);
          const f = box.getBoundingClientRect();
          const note = [...box.querySelectorAll('[data-canvas-note]')].find(n => n.getBoundingClientRect().top >= f.bottom - 1);
          note.querySelector('a, button').focus();
          return new Promise(ok => setTimeout(() => { const r = note.getBoundingClientRect(), g = box.getBoundingClientRect();
            ok({open: box.parentElement.querySelector('[data-canvas-toggle]').getAttribute('aria-expanded'), inside: r.top >= g.top - 1 && r.bottom <= g.bottom + 1}); }, 400));
        }""")
        assert reached == {"open": "true", "inside": True}, f"{label}: focusing a hidden entry gave {reached}"
    finally:
        context.close()


def _check_blocks_cut(page):
    return [b["title"] for b in _blocks(page) if b["cut"]]
