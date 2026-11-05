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


# ---------------------------------------------------------------------------------------------------------
# mobile02c: compact vendor rows on a phone, a composer gallery that fits its panel, a stylesheet that
# really emits the classes the two templates use.
#
# The committed stylesheet is not rebuilt when a branch merges, so these journeys serve a stylesheet built
# from the tree under test (the Tailwind CLI the build script uses), never the committed one.
# ---------------------------------------------------------------------------------------------------------
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture(scope="module")
def built_css(tmp_path_factory):
    """Tailwind built from this tree into a temporary file; the committed stylesheet is left alone."""
    import build_css

    cli = build_css.find_tailwind()
    assert cli, "no Tailwind CLI: put the v3 standalone binary at scripts/bin/tailwindcss"
    out = tmp_path_factory.mktemp("css") / "tailwind-output.css"
    cmd = [*cli, "--config", str(build_css.CONFIG), "--input", str(build_css.INPUT_CSS), "--output", str(out), "--minify"]
    done = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-2000:]
    return out.read_text(encoding="utf-8")


def _serve_built_css(page, css):
    page.route(re.compile(r".*tailwind-output[^/]*\.css.*"),
               lambda route: route.fulfill(status=200, body=css, headers={"content-type": "text/css"}))


VENDOR_ROW_ROUTES = ["/vendors/", "/applications/vendors"]


@pytest.mark.parametrize("route", VENDOR_ROW_ROUTES)
@pytest.mark.parametrize("theme", THEMES)
def test_a_vendor_row_is_no_taller_than_200px_on_a_phone(browser, live_server, seeded, built_css, route, theme):
    """Outcome a person needs: scroll a vendor list on a phone without one vendor filling the screen."""
    _give_a_vendor_every_column(seeded)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + route, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _set_theme(page, theme)
        page.wait_for_timeout(400)
        rows = page.evaluate("""() => Array.from(document.querySelectorAll('[data-testid="vendor-row"]')).map(r => ({
            name: (r.querySelector('[data-testid="vendor-link"]') || r).textContent.trim().slice(0, 40),
            h: Math.round(r.getBoundingClientRect().height),
            footers: r.querySelectorAll('[data-testid="result-footer"]').length}))""")
        assert rows, "no vendor rows on %s, so nothing is tested" % route
        assert any(r["footers"] for r in rows), "no vendor row on %s carries an evidence footer, so nothing is tested" % route
        tall = ["%r is %dpx tall" % (r["name"], r["h"]) for r in rows if r["h"] > 200]
        assert not tall, "%s at 390 (%s), rows over 200px:\n  %s" % (route, theme, "\n  ".join(tall))
    finally:
        page.close()


@pytest.mark.parametrize("route", VENDOR_ROW_ROUTES)
def test_a_vendors_evidence_stays_reachable_on_a_phone(browser, live_server, seeded, built_css, route):
    """Compact must not mean gone: one tap on the row's Evidence control shows the whole footer, whole."""
    _give_a_vendor_every_column(seeded)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + route, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="vendor-row"]').filter(has=page.get_by_test_id("result-footer")).first
        toggle = row.get_by_role("button", name=re.compile("evidence", re.I))
        expect(toggle).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(row.get_by_test_id("result-footer")).to_be_hidden()
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        footer = row.get_by_test_id("result-footer")
        expect(footer).to_be_visible()
        for part in ("evidence", "source", "confidence", "date"):
            expect(footer.get_by_test_id("result-footer-" + part)).to_be_visible()
        box = footer.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 391, "the opened footer runs past the screen edge: %s" % box
    finally:
        page.close()


def test_the_vendor_footer_is_unchanged_at_1440(browser, live_server, seeded, built_css):
    """Nothing changes on a desktop: the footer is shown open with no toggle, in the same place."""
    _give_a_vendor_every_column(seeded)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/vendors/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="vendor-row"]').filter(has=page.get_by_test_id("result-footer")).first
        expect(row.get_by_test_id("result-footer")).to_be_visible()
        expect(row.get_by_role("button", name=re.compile("evidence", re.I))).to_be_hidden()
    finally:
        page.close()


PANEL_JS = """
() => {
  const heading = t => Array.from(document.querySelectorAll('h4')).find(h => h.textContent.trim() === t);
  const clipBox = el => {            // the visible box: the intersection of every clipping ancestor
    let b = {l: 0, r: innerWidth, t: 0, b: innerHeight};
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.overflowY === 'visible' && cs.overflowX === 'visible') continue;
      const r = a.getBoundingClientRect();
      b = {l: Math.max(b.l, r.left), r: Math.min(b.r, r.right), t: Math.max(b.t, r.top), b: Math.min(b.b, r.bottom)};
    }
    return b;
  };
  const out = {chips: [], panel: null, cue: null, guide: null};
  for (const [group, title] of [['template', 'Start from a template'], ['recent', 'Continue where you left off']]) {
    const h = heading(title);
    if (!h) continue;
    for (const b of Array.from(h.parentElement.querySelectorAll('button')).filter(b => b.offsetParent)) {
      const r = b.getBoundingClientRect();
      out.chips.push({group, name: b.textContent.replace(/\\s+/g, ' ').trim().slice(0, 40),
                      l: r.left, r: r.right, t: r.top, b: r.bottom, clip: clipBox(b)});
    }
  }
  const first = out.chips[0];
  if (first) out.panel = first.clip;
  const cue = document.querySelector('[data-testid="composer-gallery-scroll-cue"]');
  if (cue) { const r = cue.getBoundingClientRect(); const cs = getComputedStyle(cue);
    out.cue = {w: r.width, h: r.height, t: r.top, b: r.bottom, shown: cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity > 0,
               text: cue.textContent.trim()}; }
  const tg = document.querySelector('[data-testid="composer-template-toggle"]');
  if (tg) { const r = tg.getBoundingClientRect(); out.toggle = {shown: !!tg.offsetParent && r.height > 0, text: tg.textContent.trim(),
                                                            expanded: tg.getAttribute('aria-expanded'), t: r.top, b: r.bottom}; }
  const guide = Array.from(document.querySelectorAll('summary')).find(s => /Quick start guide/.test(s.textContent));
  if (guide) { const r = guide.getBoundingClientRect(); out.guide = {t: r.top, b: r.bottom, l: r.left, r: r.right, clip: clipBox(guide)}; }
  return out;
}
"""

GALLERY_SIZES = ((1440, 900), (768, 1024), (390, 844))


def _two_saved_views(page, live_server):
    _save_a_view(page, live_server, "Lantern payments platform target state 2027")
    _save_a_view(page, live_server, "Short")


def _gallery_state(page, live_server, size, theme):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(live_server + "/archimate/composer", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _set_theme(page, theme)
    page.wait_for_timeout(800)
    return page.evaluate(PANEL_JS)


@pytest.mark.parametrize("theme", THEMES)
def test_composer_gallery_chips_are_whole_inside_the_panel_or_plainly_below_it(browser, live_server, seeded, built_css, theme):
    """No chip is cut by the canvas panel's edge; when some sit below it, a visible cue says there is more."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _two_saved_views(page, live_server)
        bad = []
        for size in GALLERY_SIZES:
            state = _gallery_state(page, live_server, size, theme)
            chips = state["chips"]
            n_tpl = sum(c["group"] == "template" for c in chips)
            if n_tpl < 12:   # only the whole chips that fit are shown, and one plain control offers all twelve
                tg = state.get("toggle")
                assert n_tpl >= 2 and tg and tg["shown"] and tg["expanded"] == "false" and re.search(r"Show all 12 templates", tg["text"]), (
                    "%s: %d template chips and no control to show the rest: %s" % (size, n_tpl, tg))
            assert sum(c["group"] == "recent" for c in chips) >= 2, "%s: the two saved views are not offered" % (size,)
            below = []
            for c in chips:
                box = c["clip"]
                inside = c["t"] >= box["t"] - 1 and c["b"] <= box["b"] + 1 and c["l"] >= box["l"] - 1 and c["r"] <= box["r"] + 1
                whole_below = c["t"] >= box["b"] - 1
                if whole_below:
                    below.append(c)
                elif not inside:
                    bad.append("%dx%d: chip %r is cut by the panel (chip %d to %d, panel %d to %d)" % (
                        size[0], size[1], c["name"], c["t"], c["b"], box["t"], box["b"]))
            if below:
                cue = state["cue"]
                if not (cue and cue["shown"] and cue["h"] > 0 and cue["w"] > 0):
                    bad.append("%dx%d: %d chip(s) sit below the panel and nothing shows there is more to scroll" % (
                        size[0], size[1], len(below)))
        assert not bad, "composer gallery (%s):\n  %s" % (theme, "\n  ".join(bad))
    finally:
        page.close()


def test_composer_gallery_shows_all_twelve_chips_and_the_guide_without_scrolling_at_1440(browser, live_server, seeded, built_css):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _two_saved_views(page, live_server)
        state = _gallery_state(page, live_server, (1440, 900), "light")
        tpl = [c for c in state["chips"] if c["group"] == "template"]
        assert len(tpl) >= 12
        out = [c["name"] for c in tpl if c["b"] > c["clip"]["b"] + 1]
        assert not out, "template chips below the panel at 1440 by 900: %s" % out
        guide = state["guide"]
        assert guide, "the Quick start guide is not on the page"
        assert guide["b"] <= guide["clip"]["b"] + 1 and guide["t"] >= guide["clip"]["t"] - 1, (
            "the Quick start guide is not visible without scrolling at 1440 by 900: %s" % guide)
    finally:
        page.close()


@pytest.mark.parametrize("size", GALLERY_SIZES)
def test_composer_quick_start_guide_is_reachable_by_scrolling(browser, live_server, seeded, built_css, size):
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/archimate/composer", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        summary = page.locator("summary", has_text="Quick start guide")
        summary.scroll_into_view_if_needed()
        expect(summary).to_be_in_viewport()
        summary.click()
        step = page.get_by_text("Drag elements from the palette onto the canvas")
        step.scroll_into_view_if_needed()
        expect(step).to_be_in_viewport()
    finally:
        page.close()


@pytest.mark.parametrize("size", GALLERY_SIZES)
def test_composer_recent_chips_overlap_neither_each_other_nor_the_templates(browser, live_server, seeded, built_css, size):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _two_saved_views(page, live_server)
        chips = _gallery_state(page, live_server, size, "light")["chips"]
        assert sum(c["group"] == "recent" for c in chips) >= 2
        bad = []
        for i, a in enumerate(chips):
            for b in chips[i + 1:]:
                if "recent" in (a["group"], b["group"]) and _intersects(a, b):
                    bad.append("%dpx: %s chip %r overlaps %s chip %r" % (size[0], a["group"], a["name"], b["group"], b["name"]))
        assert not bad, "\n  ".join(bad)
    finally:
        page.close()


def test_composer_chips_read_name_then_description_with_a_pause(browser, live_server, seeded, built_css):
    """The accessibility tree: 'Name. Description' (or the name alone with a description), never a run-on,
    and the title no longer repeats the name."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/archimate/composer", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        heading = page.locator("h4", has_text="Start from a template")
        buttons = heading.locator("xpath=..").locator("button")
        assert buttons.count() >= 12
        tree = heading.locator("xpath=..").aria_snapshot()
        names = []
        for i in range(buttons.count()):
            b = buttons.nth(i)
            name = b.locator("span").first.inner_text().strip()
            description = b.locator("span").nth(1).inner_text().strip()
            names.append(name)
            assert b.get_attribute("title") in (None, ""), "chip %r keeps a title that repeats its name" % name
            in_tree = re.search(r'button "%s(\.|")' % re.escape(name), tree)
            assert in_tree, "chip %r is not named '%s. ...' or '%s' in the accessibility tree:\n%s" % (name, name, name, tree)
            described = b.get_attribute("aria-describedby")
            run_on = re.search(r'button "%s %s' % (re.escape(name), re.escape(description[:12])), tree)
            assert not run_on, "chip %r runs its name into its description with no pause" % name
            if described:
                assert page.locator("#" + described).inner_text().strip() == description
        assert len(set(names)) == len(names)
    finally:
        page.close()


def _utility_tokens(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    tokens = set()
    for m in re.finditer(r'class="([^"]*)"', text):
        tokens.update(t.strip("'\"") for t in m.group(1).split() if "{" not in t and "%" not in t)
    return tokens


def _css_escape(token):
    return re.sub(r"([^a-zA-Z0-9_,-])", r"\\\1", token).replace(",", "\\2c ")


def test_every_variant_and_arbitrary_class_in_the_two_templates_is_emitted_by_the_built_stylesheet(built_css):
    """mobile02b shipped a grid class the stylesheet did not contain. Every class that needs the build to exist
    (a variant or an arbitrary value) in the vendor list and the composer must be emitted by a build of this tree."""
    needed = set()
    for path in ("app/templates/vendors/list.html", "app/templates/archimate/composer.html"):
        needed |= {t for t in _utility_tokens(REPO_ROOT / path) if "[" in t or t.startswith(("sm:", "max-sm:", "md:", "lg:"))}
    assert any(t.startswith("sm:") for t in needed) and any("[" in t for t in needed), "nothing to check: %s" % sorted(needed)
    missing = sorted(t for t in needed if _css_escape(t) not in built_css)
    assert not missing, "classes the templates use that a build of this tree does not emit:\n  " + "\n  ".join(missing)


def test_templates_that_do_not_fit_open_with_one_tap_and_the_panel_then_scrolls(browser, live_server, seeded, built_css):
    """Where the templates do not all fit whole, the chips that fit are shown; 'Show all 12 templates' shows the rest."""
    page = browser.new_page(viewport={"width": 768, "height": 1024})
    _serve_built_css(page, built_css)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        _two_saved_views(page, live_server)
        state = _gallery_state(page, live_server, (768, 1024), "light")
        assert 2 <= len([c for c in state["chips"] if c["group"] == "template"]) < 12, "all templates fit at 768 by 1024, so the fold is not tested"
        toggle = page.get_by_test_id("composer-template-toggle")
        expect(toggle).to_have_text(re.compile("Show all 12 templates"))
        toggle.click()
        expect(toggle).to_have_text("Show fewer templates")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        buttons = page.get_by_test_id("composer-template-grid").get_by_role("button")
        expect(buttons).to_have_count(12)
        for i in range(12):
            expect(buttons.nth(i)).not_to_have_class(re.compile(r"(^|\s)hidden(\s|$)"))
        buttons.nth(11).scroll_into_view_if_needed()
        expect(buttons.nth(11)).to_be_in_viewport()
        toggle.click()
        expect(toggle).to_have_text(re.compile("Show all 12 templates"))
    finally:
        page.close()
