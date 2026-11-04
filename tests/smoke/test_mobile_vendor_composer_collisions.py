"""At 390 wide nothing inside an overflow container may sit on top of its neighbour.

The edge check (nothing past the page edge) cannot see these: the annual cost in the vendor table drawn on
the evidence lines of the vendor cell, and the composer's template and recent-view chips overlapping each
other or cut short. The journeys assert the outcome a person needs: every figure and every name readable."""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_vendor_table_sticky_columns import _give_a_vendor_every_column, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

THEMES = ("light", "dark")


def _set_theme(page, theme):
    page.emulate_media(color_scheme=theme)
    page.evaluate("""t => { const r = document.documentElement; r.classList.toggle('dark', t === 'dark');
        r.setAttribute('data-theme', t); }""", theme)


def _intersects(a, b):
    return a["l"] < b["r"] - 1 and b["l"] < a["r"] - 1 and a["t"] < b["b"] - 1 and b["t"] < a["b"] - 1


VENDOR_JS = """
() => {
  const box = el => { const r = el.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; };
  const rows = Array.from(document.querySelectorAll('[data-testid="vendor-row"]'));
  return rows.map(row => {
    const cells = Array.from(row.querySelectorAll('td'));
    const evidence = Array.from(row.querySelectorAll('[data-testid^="result-footer"]'));
    const cost = cells.filter(c => /[£$€]\\s?\\d/.test(c.textContent)).map(c => {
      const range = document.createRange(); range.selectNodeContents(c);
      return {text: c.textContent.trim().slice(0, 40), box: box(c), content: box(range)};
    });
    const spill = cells.map(c => { const cr = c.getBoundingClientRect(); let over = 0;
      for (const el of c.querySelectorAll('*')) {
        if (el.closest('.sr-only')) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) over = Math.max(over, Math.round(r.right - cr.right));
      }
      return {text: c.textContent.trim().replace(/\\s+/g, ' ').slice(0, 40), over}; })
      .filter(x => x.over > 1);
    return {name: (row.querySelector('[data-testid="vendor-link"]') || row).textContent.trim(),
            evidence: evidence.map(e => box(e)), cost, spill};
  });
}
"""


@pytest.mark.parametrize("route", ["/applications/vendors", "/vendors/"])
@pytest.mark.parametrize("theme", THEMES)
def test_vendor_cost_never_overlaps_its_evidence_at_390(browser, live_server, seeded, route, theme):
    _give_a_vendor_every_column(seeded)
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + route, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _set_theme(page, theme)
        page.wait_for_timeout(300)
        rows = page.evaluate(VENDOR_JS)
        assert rows, "no vendor rows on %s, so nothing is tested" % route
        assert any(r["evidence"] for r in rows), "no row on %s carries an evidence footer, so nothing is tested" % route
        bad = []
        for r in rows:
            for c in r["cost"]:
                for e in r["evidence"]:
                    if _intersects(c["content"], e):
                        bad.append("row %r: cost %r overlaps its evidence lines" % (r["name"], c["text"]))
            for s in r["spill"]:
                bad.append("row %r: content of cell %r extends %dpx past its own cell" % (r["name"], s["text"], s["over"]))
        assert not bad, "%s at 390 (%s):\n  %s" % (route, theme, "\n  ".join(bad))
    finally:
        page.close()


CHIP_JS = """
() => {
  const heading = t => Array.from(document.querySelectorAll('h4')).find(h => h.textContent.trim() === t);
  const out = {};
  for (const [key, title] of [['templates', 'Start from a template'], ['recent', 'Continue where you left off']]) {
    const h = heading(title);
    const chips = h ? Array.from(h.parentElement.querySelectorAll('button')).filter(b => b.offsetParent) : [];
    out[key] = chips.map(b => {
      const r = b.getBoundingClientRect();
      const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
      let text = null, n;
      while ((n = walker.nextNode())) {
        if (!n.textContent.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(n);
        for (const q of range.getClientRects()) {
          if (q.width === 0 || q.height === 0) continue;
          if (!text) text = {l: q.left, r: q.right, t: q.top, b: q.bottom};
          else { text.l = Math.min(text.l, q.left); text.r = Math.max(text.r, q.right);
                 text.t = Math.min(text.t, q.top); text.b = Math.max(text.b, q.bottom); }
        }
      }
      const cs = getComputedStyle(b);
      return {name: b.textContent.replace(/\\s+/g, ' ').trim().slice(0, 40), text, l: r.left, r: r.right, t: r.top, b: r.bottom,
              ellipsis: Array.from(b.querySelectorAll('span')).some(s => getComputedStyle(s).textOverflow === 'ellipsis'
                                                                      && s.scrollWidth > s.clientWidth + 1)};
    });
  }
  out.page = {w: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth};
  return out;
}
"""

WIDTHS = (390, 768, 1440)


def _save_a_view(page, live_server, name):
    """Create a saved view the way a person does: add an element, press Save, name it."""
    page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.keyboard.press("Control+k")
    box = page.locator("#quick-add-input")
    expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
    box.fill("Chip check element %s" % uuid.uuid4().hex[:6])
    page.get_by_role("button", name=re.compile("^Create$")).click()
    expect(page.locator(".joint-element")).to_have_count(1, timeout=PAGE_TIMEOUT)
    page.keyboard.press("Control+s")
    modal = page.locator('[aria-label="Viewpoint name"]')
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.fill(name)
    page.get_by_role("button", name=re.compile("^Save$")).click()
    expect(modal).to_be_hidden(timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        """() => !!Alpine.$data(document.querySelector('[x-data^="composerApp"]')).currentSavedVpId""",
        timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("theme", THEMES)
def test_composer_chip_text_stays_inside_its_chip_at_every_width(browser, live_server, seeded, theme):
    view_name = "Lantern payments platform target state 2027"
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _save_a_view(page, live_server, view_name)
        bad = []
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(live_server + "/archimate/composer", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            _set_theme(page, theme)
            page.wait_for_timeout(800)
            chips = page.evaluate(CHIP_JS)
            tpl, recent = chips["templates"], chips["recent"]
            assert len(tpl) >= 2, "%d template chips at %d wide, so nothing is tested" % (len(tpl), width)
            assert recent, "no recent-view chip at %d wide: the saved view %r is not offered" % (width, view_name)
            assert any(view_name[:12] in c["name"] for c in recent), recent
            for i, a in enumerate(tpl):
                for b in tpl[i + 1:]:
                    if _intersects(a, b):
                        bad.append("%dpx: chip %r overlaps chip %r" % (width, a["name"], b["name"]))
            for c in tpl + recent:
                t = c["text"]
                if t is None:
                    bad.append("%dpx: chip %r shows no text" % (width, c["name"]))
                    continue
                if c["r"] - c["l"] < 40:
                    bad.append("%dpx: chip %r is only %dpx wide" % (width, c["name"], c["r"] - c["l"]))
                over = max(c["l"] - t["l"], t["r"] - c["r"], c["t"] - t["t"], t["b"] - c["b"])
                if over > 1:
                    bad.append("%dpx: text of chip %r leaves the chip by %dpx" % (width, c["name"], over))
                if c["ellipsis"] and c in tpl:
                    bad.append("%dpx: template chip %r is cut with an ellipsis" % (width, c["name"]))
                if t["l"] < 0 or t["r"] > chips["page"]["w"] + 1:
                    bad.append("%dpx: text of chip %r is past the screen edge" % (width, c["name"]))
        assert not bad, "composer chips (%s):\n  %s" % (theme, "\n  ".join(bad))
    finally:
        page.close()


OLD_CHIP_JS = """
() => {
  const heading = t => Array.from(document.querySelectorAll('h4')).find(h => h.textContent.trim() === t);
  const out = {};
  for (const [key, title] of [['templates', 'Start from a template'], ['recent', 'Continue where you left off']]) {
    const h = heading(title);
    const chips = h ? Array.from(h.parentElement.querySelectorAll('button')).filter(b => b.offsetParent) : [];
    out[key] = chips.map(b => { const r = b.getBoundingClientRect();
      const first = (b.querySelector('span') || b);
      return {text: b.textContent.replace(/\\s+/g, ' ').trim(), name: first.textContent.trim(),
              label: b.getAttribute('aria-label') || '', title: b.getAttribute('title') || '',
              clipped: Array.from(b.querySelectorAll('span')).some(s => s.scrollWidth > s.clientWidth + 1),
              l: r.left, r: r.right, t: r.top, b: r.bottom}; });
  }
  return out;
}
"""


@pytest.mark.parametrize("theme", THEMES)
def test_composer_template_chips_do_not_overlap_or_cut_names_at_390(browser, live_server, seeded, theme):
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/archimate/composer", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _set_theme(page, theme)
        page.wait_for_timeout(800)
        chips = page.evaluate(OLD_CHIP_JS)
        tpl = chips["templates"]
        assert len(tpl) >= 2, "the composer shows %d template chips at 390, so nothing is tested: %s" % (len(tpl), chips)
        bad = []
        for i, a in enumerate(tpl):
            for b in tpl[i + 1:]:
                if _intersects(a, b):
                    bad.append("%r overlaps %r" % (a["name"], b["name"]))
        for c in chips["templates"] + chips["recent"]:
            if c["clipped"] and c["name"] not in (c["label"], c["title"]):
                bad.append("%r is cut short and carries no full name" % c["name"])
            if c["clipped"] and not (c["label"] or c["title"]):
                bad.append("%r is cut short with no accessible name" % c["name"])
        assert not bad, "composer chips at 390 (%s):\n  %s" % (theme, "\n  ".join(bad))
    finally:
        page.close()
