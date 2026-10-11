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
  const h1 = document.querySelector('h1'), p = document.querySelector('main p');
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
        assert m["signupH"] == 44 and m["signupRadius"] == "8px", m  # P-13 button
        assert m["h1"] >= 2.75 * m["body"], m  # P-13 type scale
        assert m["docH"] > m["viewH"] and not m["mainScrolls"], m  # P-16 document scroll
        if path != "/":
            assert m["wide"] > 720 and m["prose"] <= 690, m  # P-15
    finally:
        page.close()
