"""mobile01: on a phone, no public page cuts a link, button or heading off.

Written from the visitor's goal (read every public page on a 390 px screen, light and dark). The
page list is the sitemap plus the sign-in and sign-up pages. A failure names the page and the
element, so the fix has an address.
"""

import re
import urllib.request

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PHONE = {"width": 390, "height": 844}
ACCOUNT_PAGES = ["/account/login", "/account/register"]

_AUDIT = """() => {
  const vw = document.documentElement.clientWidth, out = [];
  const label = e => (e.tagName.toLowerCase() + (e.className && typeof e.className === 'string'
    ? '.' + e.className.trim().split(/\\s+/).slice(0, 3).join('.') : '') + ' "' +
    (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60) + '"');
  const skipped = e => {
    for (let n = e; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      if (n.getAttribute('aria-hidden') === 'true' || n.hidden) return true;
      const cs = getComputedStyle(n);
      // A region that scrolls sideways on purpose (a wide table) may run past the screen.
      if (n !== e && ['auto', 'scroll'].includes(cs.overflowX)) return true;
    }
    return false;
  };
  for (const e of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    if (cs.display === 'none' || cs.visibility === 'hidden' || !r.width || !r.height) continue;
    // Screen-reader-only text is clipped to a pixel on purpose.
    if (cs.position === 'fixed' || e.closest('.sr-only') || skipped(e)) continue;
    if (r.right > vw + 1) out.push({kind: 'right edge ' + Math.round(r.right) + ' > ' + vw, el: label(e)});
    else if (e.matches('a, button, h1, h2, h3, h4, h5, h6') && e.scrollWidth > e.clientWidth + 1 &&
             cs.display !== 'inline')
      out.push({kind: 'text cut off ' + e.scrollWidth + ' > ' + e.clientWidth, el: label(e)});
  }
  return out;
}"""


def _sitemap_paths(base):
    xml = urllib.request.urlopen(base + "/sitemap.xml", timeout=60).read().decode()
    paths = [re.sub(r"^https?://[^/]+", "", u) or "/" for u in re.findall(r"<loc>([^<]+)</loc>", xml)]
    assert paths, "the sitemap lists no pages"
    return paths


def _audit(browser, base, scheme):
    ctx = browser.new_context(viewport=PHONE, color_scheme=scheme)
    ctx.add_init_script(
        "try{localStorage.setItem('theme','%s')}catch(e){}" % scheme)
    page = ctx.new_page()
    failures = []
    try:
        for path in dict.fromkeys(_sitemap_paths(base) + ACCOUNT_PAGES):
            resp = page.goto(base + path, timeout=PAGE_TIMEOUT)
            if resp is None or resp.status >= 400:
                failures.append(f"{path}: answered {resp.status if resp else 'nothing'}")
                continue
            page.evaluate("s => document.documentElement.classList.toggle('dark', s === 'dark')", scheme)
            page.wait_for_timeout(100)
            for f in page.evaluate(_AUDIT):
                failures.append(f"{path} [{scheme}]: {f['kind']}: {f['el']}")
    finally:
        ctx.close()
    return failures


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_public_page_overflows_or_cuts_text_at_390(browser, live_server, scheme):
    failures = _audit(browser, live_server, scheme)
    assert not failures, f"{len(failures)} problems at 390 wide:\n" + "\n".join(failures)


@pytest.mark.parametrize("path", ACCOUNT_PAGES)
def test_sign_in_and_sign_up_each_have_one_visible_h1(browser, live_server, path):
    page = browser.new_page(viewport=PHONE)
    try:
        assert page.goto(live_server + path, timeout=PAGE_TIMEOUT).status == 200
        h1 = page.locator("main h1")
        assert h1.count() == 1, f"{path} has {h1.count()} h1"
        assert h1.first.is_visible()
        assert h1.first.inner_text().strip() in ("Welcome back", "Create an account")
    finally:
        page.close()


def test_sign_up_on_a_phone_links_the_logo_home(browser, live_server):
    page = browser.new_page(viewport=PHONE)
    try:
        assert page.goto(live_server + "/account/register", timeout=PAGE_TIMEOUT).status == 200
        logo = page.locator("main a[href='/']:has(svg[role=img])")
        assert logo.count() == 1 and logo.first.is_visible()
        logo.first.click()
        page.wait_for_load_state()
        assert page.url.rstrip("/") == live_server.rstrip("/")
    finally:
        page.close()
