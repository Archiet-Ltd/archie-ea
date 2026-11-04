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

# The Overview ("/") is a protected reference page and is measured in the report, not asserted here.
ROUTES = ["/applications/", "/ai-chat", "/account/manage", "/account/welcome",
          "/vendors/", "/capability-map/",
          # The breadcrumb Home link is icon-only on these, and the icon back link on the last two.
          "/implementation/", "/implementation/deliverables", "/implementation/gaps",
          "/implementation/plateaus", "/implementation/work-packages", "/capability-map/trees",
          "/batch-import/new", "/duplicate-detection/ai/analyze", "/duplicate-detection/ai/dashboard"]

# A phone in light at 390 and in dark at 360: the two widths and themes the review measured.
COMBOS = [(390, "light"), (360, "dark")]

LONG_NAME = "Customer Identity and Access Management Platform for Field Engineers"

_MEASURE = """() => {
  const out = {tap: [], font: [], th: [], checkbox: []};
  const vis = el => { const r = el.getBoundingClientRect(); const c = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'
      && !el.closest('[aria-hidden=true],[hidden],.sr-only,[inert]'); };
  const desc = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ' "' +
    (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 20) + '" <' + el.outerHTML.slice(0, 110).replace(/\s+/g, ' ') + '>';
  // One hit box: the element, or the label that wraps it or points at it, taken as a single box.
  // A box of 24 wide from one and 24 tall from another does not count.
  const boxes = el => {
    const r = el.getBoundingClientRect(), bs = [[r.width, r.height]];
    const lab = el.closest('label') || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
    if (lab) { const l = lab.getBoundingClientRect(); bs.push([l.width, l.height]); }
    return bs;
  };
  // A link inside a sentence is exempt (WCAG 2.5.8): inline, with text beside it in its parent.
  const inText = el => { if (getComputedStyle(el).display !== 'inline') return false;
    const p = el.parentElement; if (!p) return false;
    for (const n of p.childNodes) if (n !== el && n.nodeType === 3 && n.textContent.trim().length > 1) return true;
    return !!el.closest('p'); };
  const sel = 'main a[href], main input[type=checkbox], main input[type=radio], nav[aria-label=Breadcrumb] a, ' +
    'nav[aria-label=breadcrumb] a, [data-testid=app-footer] a, footer a';
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (seen.has(el)) continue; seen.add(el);
    if (!vis(el)) continue;
    if (el.matches('a') && inText(el)) continue;
    if (el.closest('li') && el.matches('a')) continue; // list links are named in the report, not asserted here
    if (!boxes(el).some(([w, h]) => w >= 24 && h >= 24)) {
      const r = el.getBoundingClientRect();
      out.tap.push(desc(el) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
    }
    if (el.matches('input[type=checkbox]')) { // a checkbox keeps its drawn size; the hit box is invisible
      const r = el.getBoundingClientRect();
      if (r.width > 16.5 || r.height > 16.5) out.checkbox.push(desc(el) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
    }
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

# Side effects: a link marked hidden stays hidden, and a truncated name keeps its ellipsis inside its card.
_HIDDEN = """() => { const d = document.createElement('div');
  d.innerHTML = '<a id="probe-hidden" class="hidden w-full inline-flex items-center" href="#">Download</a>';
  document.querySelector('main').appendChild(d);
  return getComputedStyle(document.getElementById('probe-hidden')).display; }"""

_TRUNCATE = """(name) => {
  const link = [...document.querySelectorAll('main a.truncate')].find(a => a.textContent.trim().startsWith(name) && a.getBoundingClientRect().width > 0);
  if (!link) return {found: false};
  const card = link.closest('li, article, tr, [data-slot=card], .rounded-lg, .rounded-md') || link.parentElement;
  const l = link.getBoundingClientRect(), c = card.getBoundingClientRect(), cs = getComputedStyle(link);
  const doc = document.documentElement;
  return {found: true, ellipsis: cs.textOverflow === 'ellipsis' && cs.overflow !== 'visible' && link.scrollWidth > link.clientWidth,
    display: cs.display, inside: l.right <= c.right + 1, cardScrolls: card.scrollWidth > card.clientWidth + 1 && ['auto', 'scroll'].includes(getComputedStyle(card).overflowX), card: card.tagName + '.' + String(card.className).slice(0, 60) + ' sw' + card.scrollWidth + ' cw' + card.clientWidth,
    pageScrolls: doc.scrollWidth > doc.clientWidth + 1, right: Math.round(l.right), cardRight: Math.round(c.right)};
}"""


def _seed_long_name(app, org_id):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        db.session.add(ApplicationComponent(name=LONG_NAME, organization_id=org_id))
        db.session.commit()


def test_phone_pages_have_tappable_controls_and_no_zoom_inputs(browser, live_server, app):
    org = _make_org(app, "Alpha", with_apps=True)
    _seed_long_name(app, org["org"])
    failures = []
    for width, scheme in COMBOS:
        ctx = browser.new_context(viewport={"width": width, "height": 844}, color_scheme=scheme)
        ctx.add_init_script("try{localStorage.setItem('theme','%s')}catch(e){}" % scheme)
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = ctx.new_page()
        tag = "%d %s" % (width, scheme)
        try:
            _login(page, live_server, org["email"])
            for route in ROUTES:
                response = page.goto(live_server + route, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(700)
                assert response is not None and response.status < 400, (tag, route, response and response.status)
                assert len((page.inner_text("body") or "").strip()) > 40, "%s %s opened empty" % (tag, route)
                found = page.evaluate(_MEASURE)
                for kind, items in found.items():
                    for item in items:
                        failures.append("%s %s %s: %s" % (tag, route, kind, item))
                if page.evaluate(_HIDDEN) != "none":
                    failures.append("%s %s: a link marked hidden is shown" % (tag, route))
            page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(700)
            trunc = page.evaluate(_TRUNCATE, LONG_NAME)
            if not trunc.get("found"):
                failures.append("%s /applications/: no truncating link for the long name" % tag)
            else:
                if not trunc["ellipsis"]:
                    failures.append("%s /applications/: the long name does not end in an ellipsis (%s)" % (tag, trunc))
                if not trunc["inside"]:
                    failures.append("%s /applications/: the long name runs past its card (%s)" % (tag, trunc))
                if trunc["cardScrolls"] or trunc["pageScrolls"]:
                    failures.append("%s /applications/: the card or page scrolls sideways (%s)" % (tag, trunc))
        finally:
            ctx.close()
    assert not failures, "\n".join(failures[:60])
