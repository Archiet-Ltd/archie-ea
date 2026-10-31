"""Every class name on a signed-in page has a rule in a stylesheet that page loads.

A platform administrator in an organisation holding linked records opens the Overview, the
value streams list and one value stream, the ARB sessions, the AI chat, the applications and the
team, at a desktop and a phone width, in the light and the dark theme. On each page the check
lists every class name on any element that no loaded stylesheet has a rule for. A class with no
rule does nothing: a card header holding an action is not laid out in two columns, an invalid
field has no red ring, a disabled menu item looks like an enabled one.
"""
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = (("desktop", 1440, 900), ("phone", 390, 844))
THEMES = ("light", "dark")

# Names that carry no style on purpose: they mark an element for a parent or sibling
# selector, name an icon, or are hooks for a script or a test. Everything else is a
# styling class and must have a rule.
MARKERS = re.compile(
    r"^((group|peer|dark|light|active|lucide|xAxis|yAxis|row-select|tab-button)(/[\w-]+)?"
    r"|(lucide|cn|js|bu|sidebar|recharts)-[\w-]+)$")

COLLECT = """
() => {
  const named = new Set();
  const pick = (text) => {
    for (const m of text.matchAll(/\\.((?:\\\\.|[\\w-])+)/g)) named.add(m[1].replace(/\\\\(.)/g, '$1'));
  };
  const walk = (rules) => {
    for (const r of rules) {
      if (r.selectorText) pick(r.selectorText);
      if (r.cssRules) walk(r.cssRules);
    }
  };
  let unreadable = 0;
  for (const sheet of document.styleSheets) {
    try { walk(sheet.cssRules); } catch (e) { unreadable += 1; }
  }
  const missing = {};
  for (const el of document.querySelectorAll('body *')) {
    if (!(el.classList && el.classList.length)) continue;
    for (const c of el.classList) if (!named.has(c)) missing[c] = (missing[c] || 0) + 1;
  }
  return {missing, unreadable};
}
"""


def _csrf(page):
    return page.locator('meta[name="csrf-token"]').get_attribute("content") or ""


@pytest.fixture(scope="module")
def pages(live_server, seeded, browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", seeded["emails"]["platform_admin"])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    page.goto(live_server + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    resp = page.request.post(
        live_server + "/value-streams/create",
        form={"name": "Order to cash", "csrf_token": _csrf(page)},
    )
    assert resp.ok, resp.text()[:300]
    match = re.search(r"/value-streams/(\d+)", resp.url)
    assert match, "the new value stream did not land on its own page: %s" % resp.url
    yield page, {
        "Overview": "/dashboard/overview",
        "Value streams": "/value-streams/",
        "Value stream detail": "/value-streams/%s" % match.group(1),
        "ARB sessions": "/arb/sessions",
        "AI chat": "/ai-chat",
        "Applications": "/applications/",
        "Team": "/admin/team",
    }
    context.close()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_no_class_without_a_rule(live_server, pages, size, theme):
    page, paths = pages
    _, width, height = size
    page.set_viewport_size({"width": width, "height": height})
    unstyled = {}
    for title, path in paths.items():
        page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert "/account/login" not in page.url, "%s sent the administrator to sign in" % title
        assert page.locator("main, [role=main]").first.inner_text().strip(), "%s is empty" % title
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        found = page.evaluate(COLLECT)
        assert found["unreadable"] == 0, "%s: %d stylesheets could not be read" % (title, found["unreadable"])
        names = sorted(c for c in found["missing"] if not MARKERS.match(c))
        if names:
            unstyled[title] = names
    assert not unstyled, "classes with no rule in a loaded stylesheet (%s, %s):\n%s" % (
        size[0], theme, "\n".join("  %s (%d): %s" % (t, len(n), ", ".join(n)) for t, n in unstyled.items()))
