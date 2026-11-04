"""A startup CTO on a phone can tap every control and type without the page zooming.

The goal: at 390 wide, on the pages a newcomer opens first, every link and button in
the breadcrumb, footer and main content is at least 24 by 24 CSS px (the element or
its hit box), no text input, select or textarea has computed text under 16 px (iOS
zooms the page on focus below that), and no table header is under 12 px.
"""

import pytest

from .conftest import PAGE_TIMEOUT
from .test_applications_list_says_what_to_do_journey import _login, _make_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROUTES = ["/", "/applications/", "/ai-chat", "/account/manage", "/account/welcome",
          "/vendors/", "/capability-map/"]

_MEASURE = """() => {
  const out = {tap: [], font: [], th: []};
  const vis = el => { const r = el.getBoundingClientRect(); const c = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'
      && !el.closest('[aria-hidden=true],[hidden],.sr-only,[inert]'); };
  const desc = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' "' +
    (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 20) + '"';
  // A control's hit box: itself, or the label / link that wraps or points at it.
  const hit = el => {
    let r = el.getBoundingClientRect(), w = r.width, h = r.height;
    const lab = el.closest('label') || (el.id && document.querySelector('label[for="' + el.id + '"]'));
    if (lab) { const l = lab.getBoundingClientRect(); w = Math.max(w, l.width); h = Math.max(h, l.height); }
    const after = getComputedStyle(el, '::after');
    if (after.position === 'absolute' && el.matches('a,button,input')) {
      const aw = parseFloat(after.width), ah = parseFloat(after.height);
      if (aw) w = Math.max(w, aw); if (ah) h = Math.max(h, ah);
    }
    return [w, h];
  };
  for (const el of document.querySelectorAll('main a[href], main button, nav[aria-label=Breadcrumb] a, footer a, main input[type=checkbox], main input[type=radio]')) {
    if (!vis(el)) continue;
    if (el.closest('p, li > span, td > span') && el.matches('a') && getComputedStyle(el).display === 'inline') continue; // inline text links are exempt (WCAG 2.5.8)
    const [w, h] = hit(el);
    if (w < 24 || h < 24) out.tap.push(desc(el) + ' ' + Math.round(w) + 'x' + Math.round(h));
  }
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!vis(el) || ['checkbox', 'radio', 'submit', 'button', 'hidden', 'file', 'range', 'color'].includes(el.type)) continue;
    if (parseFloat(getComputedStyle(el).fontSize) < 16) out.font.push(desc(el) + ' ' + getComputedStyle(el).fontSize);
  }
  for (const el of document.querySelectorAll('th')) {
    if (vis(el) && parseFloat(getComputedStyle(el).fontSize) < 12) out.th.push(desc(el) + ' ' + getComputedStyle(el).fontSize);
  }
  return out;
}"""


def test_phone_pages_have_tappable_controls_and_no_zoom_inputs(browser, live_server, app):
    org = _make_org(app, "Alpha", with_apps=True)
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    failures = []
    try:
        _login(page, live_server, org["email"])
        for route in ROUTES:
            response = page.goto(live_server + route, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(700)
            assert response is not None and response.status < 400, (route, response and response.status)
            assert len((page.inner_text("body") or "").strip()) > 40, "%s opened empty" % route
            found = page.evaluate(_MEASURE)
            for kind, items in found.items():
                for item in items:
                    failures.append("%s %s: %s" % (route, kind, item))
    finally:
        ctx.close()
    assert not failures, "\n".join(failures[:60])
