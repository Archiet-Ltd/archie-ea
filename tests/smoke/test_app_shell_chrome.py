"""The app shell's chrome, measured in a real browser: header band, menu button,
drawer shadow, help button, and the AI chat's fit under the header.

None of these is visible to a source scan or to the accessibility tree: the header
must be 64 px on a short page and on a page taller than the viewport, the fixed menu
button (12..52 px) must sit inside that band, the closed drawer must not tint the
left edge of the page below lg, the floating help button must not cover the last row
of a scrolled dashboard, and AI chat must leave no strip of bare page background
under its composer.

The markup half of this contract (the classes that produce these numbers) is pinned
in tests/test_app_shell_chrome.py.
"""
import io

import pytest
from PIL import Image

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Pages that render the shell: some shorter than the viewport, some taller.
PAGES = ["/dashboard/overview", "/arb/", "/solutions/", "/architecture/dashboard", "/ai-chat"]

HEADER_HEIGHT = 64

_MEASURE = """() => {
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
                     return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height}; };
  const shown = e => { if (!e) return false; const r = e.getBoundingClientRect();
                       return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none'; };
  const header = document.getElementById('admin-header');
  const menu = document.querySelector('button[aria-label="Open sidebar"]');
  const sidebar = document.getElementById('admin-sidebar');
  const scroller = header ? header.parentElement : null;
  const chat = document.querySelector('[x-data*="railOpen"]');
  const frame = document.querySelector('[data-testid="ai-chat-frame"]');
  return {
    chat: box(chat), frame: box(frame),
    header: box(header), menu: box(menu), menuShown: shown(menu),
    sidebarShadow: sidebar ? getComputedStyle(sidebar).boxShadow : null,
    scroller: scroller ? {client: scroller.clientHeight, scroll: scroller.scrollHeight} : null,
    innerHeight: window.innerHeight,
  };
}"""

# Text nodes that sit under the floating help button, as painted (not merely laid out).
_UNDER_HELP = """() => {
  const help = document.getElementById('guided-mode-trigger');
  if (!help) return null;
  const hr = help.getBoundingClientRect();
  const hits = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const text = (n.nodeValue || '').replace(/\\s+/g, ' ').trim();
    const el = n.parentElement;
    if (!text || !el || help.contains(el) || el.closest('script,style,template,noscript')) continue;
    if (getComputedStyle(el).display === 'none' || getComputedStyle(el).visibility === 'hidden') continue;
    const range = document.createRange(); range.selectNodeContents(n);
    for (const r of range.getClientRects()) {
      if (r.width && r.height && Math.min(r.right, hr.right) > Math.max(r.left, hr.left)
          && Math.min(r.bottom, hr.bottom) > Math.max(r.top, hr.top)) hits.push(text.slice(0, 60));
    }
  }
  return {help: [hr.left, hr.top, hr.right, hr.bottom], hits};
}"""


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open(page, base, path, width, height):
    page.set_viewport_size({"width": width, "height": height})
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, "%s -> %s" % (path, response.status)
    page.wait_for_selector("#admin-header", timeout=PAGE_TIMEOUT)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass                                    # long-poll / chart fetches; the layout has settled by now
    page.wait_for_timeout(800)


@pytest.fixture(scope="module")
def solution_architect_page(browser, live_server, seeded):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    _login(page, live_server, seeded["emails"]["solution_architect"])
    yield page
    context.close()


@pytest.mark.parametrize("path", PAGES)
def test_header_is_64px_on_every_page(solution_architect_page, live_server, path):
    _open(solution_architect_page, live_server, path, 1440, 900)
    m = solution_architect_page.evaluate(_MEASURE)
    assert m["header"]["height"] == HEADER_HEIGHT, (
        "%s: header is %spx tall; it must not shrink with the page (scroll %s of %s)"
        % (path, m["header"]["height"], m["scroller"]["scroll"], m["scroller"]["client"])
    )


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("width,height", [(768, 900), (390, 844)])
def test_menu_button_sits_inside_the_header_band(solution_architect_page, live_server, path, width, height):
    _open(solution_architect_page, live_server, path, width, height)
    solution_architect_page.wait_for_selector('button[aria-label="Open sidebar"]', state="visible",
                                              timeout=PAGE_TIMEOUT)
    m = solution_architect_page.evaluate(_MEASURE)
    assert m["header"]["height"] == HEADER_HEIGHT, m["header"]
    assert m["menu"]["bottom"] <= m["header"]["bottom"], (
        "%s at %d: menu button bottom %s is below the header rule at %s"
        % (path, width, m["menu"]["bottom"], m["header"]["bottom"])
    )
    assert m["menu"]["top"] >= m["header"]["top"], m


def _left_edge_shade(png, columns=13, ignore_rows=()):
    im = Image.open(io.BytesIO(png)).convert("RGB")
    px = im.load()
    width, height = im.size
    shaded = 0
    for y in range(height):
        if any(a <= y <= b for a, b in ignore_rows):
            continue
        for x in range(columns):
            if px[x, y] != (255, 255, 255):
                shaded += 1
    return shaded


def test_closed_drawer_casts_no_shade_and_open_drawer_keeps_its_shadow(solution_architect_page, live_server):
    page = solution_architect_page
    _open(page, live_server, "/dashboard/overview", 390, 844)
    page.wait_for_selector('button[aria-label="Open sidebar"]', state="visible", timeout=PAGE_TIMEOUT)
    m = page.evaluate(_MEASURE)
    assert m["sidebarShadow"] == "none", "closed drawer still carries a box-shadow: %s" % m["sidebarShadow"]
    rule = int(m["header"]["bottom"])
    shaded = _left_edge_shade(page.screenshot(), ignore_rows=[(rule - 2, rule + 1)])
    assert shaded == 0, "%d pixels in columns 0-12 differ from the page background" % shaded

    page.click('button[aria-label="Open sidebar"]')
    page.wait_for_function(
        "() => { const s = document.getElementById('admin-sidebar').getBoundingClientRect(); return s.left >= 0; }",
        timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(400)                   # the 200 ms slide and shadow transition
    opened = page.evaluate(_MEASURE)
    assert opened["sidebarShadow"] not in (None, "none"), "an open drawer must still cast its shadow"


@pytest.mark.parametrize("persona", ["enterprise_architect", "procurement", "arb_member", "application_manager"])
def test_help_button_does_not_cover_text_at_the_end_of_the_page(browser, live_server, seeded, persona):
    """Scrolled to the end of the dashboard, the last row (a metric value, a tile
    label) sits above the floating help button instead of under it."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.set_default_timeout(PAGE_TIMEOUT)
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"][persona])
        _open(page, live_server, "/dashboard/overview", 390, 844)
        page.wait_for_selector("#guided-mode-trigger", state="visible", timeout=PAGE_TIMEOUT)
        page.evaluate("() => { const s = document.getElementById('admin-header').parentElement;"
                      " s.scrollTop = s.scrollHeight; }")
        page.wait_for_timeout(400)
        under = page.evaluate(_UNDER_HELP)
        assert under and under["hits"] == [], "help button %s covers: %s" % (under and under["help"], under and under["hits"])
    finally:
        context.close()


def test_ai_chat_fills_the_area_under_the_header_with_nothing_left_to_scroll(solution_architect_page, live_server):
    """With or without the "AI provider unavailable" notice (a fresh install has it, an
    installation with a provider does not), the chat and its notice are exactly the area
    under the header: nothing to scroll, and no bare page background under the composer."""
    page = solution_architect_page
    _open(page, live_server, "/ai-chat", 1440, 900)
    m = page.evaluate(_MEASURE)
    assert m["scroller"]["scroll"] <= m["scroller"]["client"], (
        "AI chat overflows the shell by %spx" % (m["scroller"]["scroll"] - m["scroller"]["client"])
    )
    assert abs(m["frame"]["top"] - m["header"]["bottom"]) < 1, (
        "chat starts at %s, header ends at %s" % (m["frame"]["top"], m["header"]["bottom"])
    )
    assert abs(m["chat"]["bottom"] - m["innerHeight"]) < 1, (
        "chat ends at %s in a %s px viewport: bare page background shows under the composer"
        % (m["chat"]["bottom"], m["innerHeight"])
    )
    # And in pixels: the last row is the composer footer's colour, not the page's.
    png = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    height = png.size[1]
    for x in (700, 900, 1100):                   # inside the chat pane, clear of the send buttons
        bottom, above = png.getpixel((x, height - 1)), png.getpixel((x, height - 12))
        assert bottom == above, (
            "a strip of different colour %s (vs %s above it) sits under the composer at x=%d" % (bottom, above, x)
        )
