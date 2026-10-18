"""Public marketing pages: shared template craft (nav alignment, one button system,
one type scale, wide containers, document scroll). Real rendered measurements."""

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_MEASURE = """() => {
  const R = e => e.getBoundingClientRect(), cs = e => getComputedStyle(e);
  const mid = e => R(e).top + R(e).height / 2;
  const link = [...document.querySelectorAll('nav a')].find(a => /Features/.test(a.innerText));
  const signin = [...document.querySelectorAll('a')].find(a => a.innerText.trim() === 'Sign In');
  const signup = [...document.querySelectorAll('a')].find(a => a.innerText.trim() === 'Sign Up');
  // Body text is a paragraph of page content: on a landing page the first <p> in <main> is
  // the hero lead (a larger sub-line), so prefer the first content paragraph when there is one.
  const h1 = document.querySelector('h1'),
        p = document.querySelector('main .public-page-content p') || document.querySelector('main p');
  const main = document.querySelector('main#main-content');
  return {
    linkMid: mid(link), signinMid: mid(signin), signupH: R(signup).height, signupRadius: cs(signup).borderRadius,
    h1: parseFloat(cs(h1).fontSize), body: parseFloat(cs(p).fontSize),
    docH: document.documentElement.scrollHeight, viewH: innerHeight,
    mainScrolls: main.scrollHeight > main.clientHeight + 1 && ['auto', 'scroll'].includes(cs(main).overflowY),
    wide: Math.max(...[...document.querySelectorAll('.public-container')].map(e => R(e).width)),
    prose: R(p).width,
  };
}"""


@pytest.mark.parametrize("path", ["/", "/pricing", "/modules/arb"])
def test_public_pages_share_one_craft_system(browser, live_server, path):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        assert page.goto(live_server + path, timeout=PAGE_TIMEOUT).status == 200
        m = page.evaluate(_MEASURE)
        assert abs(m["linkMid"] - m["signinMid"]) <= 2, m  # P-01
        # The public button system is 40px tall with a 14px radius. The navbar moves to the 36px
        # size in the next change, which updates this line.
        assert m["signupH"] == 40 and m["signupRadius"] == "14px", m
        assert m["h1"] >= 2.75 * m["body"], m  # P-13 type scale
        assert m["docH"] > m["viewH"] and not m["mainScrolls"], m  # P-16 document scroll
        if path != "/":
            assert m["wide"] > 720 and m["prose"] <= 690, m  # P-15
    finally:
        page.close()


_FOCUS = """sel => {
  const e = document.querySelector(sel); e.focus();
  const c = getComputedStyle(e);
  return {width: parseFloat(c.outlineWidth), style: c.outlineStyle};
}"""


@pytest.mark.parametrize("forced", ["none", "active"])
@pytest.mark.parametrize("sel", [".public-btn--primary", ".public-btn--outline", ".public-link", ".public-faq summary"])
def test_public_focus_indicator_is_a_solid_outline(browser, live_server, sel, forced):
    """A focused control shows a real outline (a box-shadow is dropped in forced-colors mode)."""
    ctx = browser.new_context(forced_colors=forced, viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        assert page.goto(live_server + "/", timeout=PAGE_TIMEOUT).status == 200
        page.keyboard.press("Tab")
        page.evaluate(_FOCUS, sel)
        page.wait_for_timeout(400)  # buttons transition all properties over 150ms
        m = page.evaluate(_FOCUS, sel)
        assert m["style"] == "solid" and m["width"] >= 2, m
    finally:
        ctx.close()


def test_pricing_table_scroll_region_is_keyboard_reachable(browser, live_server):
    """Narrow screens hide the right-hand columns; the wrapper must take focus and scroll by keyboard."""
    page = browser.new_page(viewport={"width": 375, "height": 800})
    try:
        assert page.goto(live_server + "/", timeout=PAGE_TIMEOUT).status == 200
        region = page.locator("[role=region][aria-label^='Pricing']")
        assert region.count() == 1 and region.get_attribute("tabindex") == "0"
        region.focus()
        for _ in range(40):
            page.keyboard.press("ArrowRight")
        page.wait_for_timeout(800)
        m = page.evaluate("""() => {
          const w = document.activeElement, th = [...w.querySelectorAll('th')].pop();
          return {focused: w.getAttribute('role') === 'region', right: th.getBoundingClientRect().right,
                  edge: w.getBoundingClientRect().right};
        }""")
        assert m["focused"] and m["right"] <= m["edge"] + 1, m
    finally:
        page.close()
