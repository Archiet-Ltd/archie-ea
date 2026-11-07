"""Journey, in a browser: a person on a phone (390 and 360 wide, light and dark) can reach every tab of every strip.

A strip wider than the screen scrolls sideways inside its own box and fades at the edge that hides more tabs; the
open tab is in view when the page loads and after the last tab is chosen; every tab is tall enough to touch and the
arrow keys and the Tab key reach all of them.
"""

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WIDTHS = (390, 360)
SCHEMES = ("light", "dark")

# Pages with a tab strip: (name, path, role). {application} and {solution} are the seeded organisation's records.
ROUTES = [
    ("my-applications-health", "/my-applications/health", "application_manager"),
    ("my-applications-roadmap", "/my-applications/roadmap", "application_manager"),
    ("rationalization", "/applications/rationalization", "platform_admin"),
    ("rationalization-gaps", "/applications/rationalization/gaps", "platform_admin"),
    ("rationalization-tracking", "/applications/rationalization/tracking", "platform_admin"),
    ("rationalization-workbench", "/applications/rationalization/workbench", "platform_admin"),
    ("rationalization-enrich", "/applications/rationalization/enrich", "platform_admin"),
    ("rationalization-planning", "/applications/rationalization/planning/{application}", "platform_admin"),
    ("team", "/admin/team", "platform_admin"),
    ("backlog-prioritisation", "/backlog/prioritisation", "platform_admin"),
    ("definition-of-done", "/dod/templates", "platform_admin"),
    ("application-record", "/applications/{application}", "platform_admin"),
    ("application-roadmap", "/applications/{application}/roadmap", "platform_admin"),
    ("solution-record", "/solutions/{solution}", "solution_architect"),
    ("governance-dashboard", "/governance/dashboard", "platform_admin"),
    ("settings", "/settings", "platform_admin"),
]

STRIPS = """() => {
  const sel = '[role=tab], [data-slot=tabs-trigger]';
  const open = t => t.getAttribute('aria-selected') === 'true' || t.getAttribute('aria-current') === 'page' ||
                    t.getAttribute('data-active') === 'true';
  const strips = Array.from(document.querySelectorAll('[role=tablist], [data-tab-strip]'))
    .filter(s => s.offsetParent !== null && s.querySelectorAll(sel).length > 1)
    .filter((s, i, all) => !all.some(o => o !== s && o.contains(s)));
  return strips.map((s, index) => {
    const box = s.getBoundingClientRect(), cs = getComputedStyle(s);
    const tabs = Array.from(s.querySelectorAll(sel)).filter(t => t.offsetParent !== null);
    const mask = cs.maskImage || cs.webkitMaskImage || 'none';
    return {
      index, label: s.getAttribute('aria-label') || tabs.map(t => t.textContent.trim()).join(' | ').slice(0, 60),
      scrolls: s.scrollWidth > s.clientWidth + 1, overflowX: cs.overflowX, fade: mask !== 'none',
      within: box.left >= -1 && box.right <= window.innerWidth + 1,
      tabs: tabs.map(t => {
        const r = t.getBoundingClientRect();
        return {text: t.textContent.trim().slice(0, 30), height: r.height, open: open(t),
                disabled: t.getAttribute('aria-disabled') === 'true' || t.disabled === true,
                href: t.getAttribute('href'), tabIndex: t.tabIndex,
                whole: r.left >= box.left - 1 && r.right <= box.right + 1};
      })};
  });
}"""


def _problems(page, where):
    out = []
    strips = page.evaluate(STRIPS)
    if not strips:
        return ["%s: no tab strip found" % where]
    for s in strips:
        name = "%s, strip '%s'" % (where, s["label"])
        if not s["within"]:
            out.append("%s runs past the screen" % name)
        every_tab_whole = all(t["whole"] for t in s["tabs"])
        if not every_tab_whole and not (s["scrolls"] and s["overflowX"] in ("auto", "scroll") and s["fade"]):
            out.append("%s: tabs are cut off and the strip neither scrolls nor shows a fade "
                       "(scrolls=%s overflow-x=%s fade=%s)" % (name, s["scrolls"], s["overflowX"], s["fade"]))
        shown = [t for t in s["tabs"] if t["open"]]
        if shown and not all(t["whole"] for t in shown):
            out.append("%s: the open tab '%s' is not fully in view" % (name, shown[0]["text"]))
        for t in s["tabs"]:
            if t["height"] < 24:
                out.append("%s: tab '%s' is %.0f px high" % (name, t["text"], t["height"]))
            if t["tabIndex"] < 0:
                out.append("%s: tab '%s' cannot be reached with the Tab key" % (name, t["text"]))
    return out


def _arrows_reach_every_tab(page, where):
    """Focus the first tab of each strip and walk to the last with ArrowRight."""
    out = []
    for index in range(len(page.evaluate(STRIPS))):
        names = page.evaluate("""(index) => {
          const sel = '[role=tab], [data-slot=tabs-trigger]';
          const s = Array.from(document.querySelectorAll('[role=tablist], [data-tab-strip]'))
            .filter(e => e.offsetParent !== null && e.querySelectorAll(sel).length > 1)
            .filter((e, i, all) => !all.some(o => o !== e && o.contains(e)))[index];
          const tabs = Array.from(s.querySelectorAll(sel)).filter(t => t.offsetParent !== null);
          tabs[0].focus();
          return tabs.map(t => t.textContent.trim());
        }""", index)
        reached = [page.evaluate("() => document.activeElement.textContent.trim()")]
        for _ in names[1:]:
            page.keyboard.press("ArrowRight")
            reached.append(page.evaluate("() => document.activeElement.textContent.trim()"))
        if reached != names:
            out.append("%s, strip %d: the arrow keys reached %s, not %s" % (where, index, reached, names))
    return out


def _choose_last_tab(page, where):
    """Choose the last enabled tab of each strip that switches in place; for a strip of links follow the last link."""
    out = []
    for index in range(len(page.evaluate(STRIPS))):
        last = page.evaluate(STRIPS)[index]["tabs"]
        last = [t for t in last if not t["disabled"]][-1]
        if last["href"]:
            continue
        loc = page.evaluate("""([index, text]) => {
          const sel = '[role=tab], [data-slot=tabs-trigger]';
          const s = Array.from(document.querySelectorAll('[role=tablist], [data-tab-strip]'))
            .filter(e => e.offsetParent !== null && e.querySelectorAll(sel).length > 1)
            .filter((e, i, all) => !all.some(o => o !== e && o.contains(e)))[index];
          const tabs = Array.from(s.querySelectorAll(sel)).filter(t => t.offsetParent !== null && t.getAttribute('aria-disabled') !== 'true');
          const t = tabs[tabs.length - 1];
          t.scrollIntoView({inline: 'nearest', block: 'nearest'});
          const r = t.getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        }""", [index, last["text"]])
        page.mouse.click(loc[0], loc[1])
        page.wait_for_timeout(500)
        strip = page.evaluate(STRIPS)[index]
        chosen = [t for t in strip["tabs"] if t["open"]]
        if not chosen or chosen[0]["text"] != last["text"]:
            out.append("%s, strip %d: choosing '%s' did not open it" % (where, index, last["text"]))
        elif not chosen[0]["whole"]:
            out.append("%s, strip %d: the chosen last tab '%s' is not fully in view" % (where, index, last["text"]))
    return out


@pytest.fixture(scope="module")
def signed_in(browser, live_server, seeded):
    contexts = {}

    def get(role):
        if role not in contexts:
            ctx = browser.new_context()
            page = ctx.new_page()
            fresh_org.sign_in(page, live_server, seeded["emails"][role])
            page.close()
            contexts[role] = ctx
        return contexts[role]
    yield get
    for ctx in contexts.values():
        ctx.close()


@pytest.mark.parametrize("name,path,role", ROUTES, ids=[r[0] for r in ROUTES])
def test_every_tab_is_reachable_on_a_phone(signed_in, live_server, seeded, name, path, role):
    url = live_server + path.format(application=seeded["ids"]["application"], solution=seeded["ids"]["solution"])
    problems = []
    ctx = signed_in(role)
    for width in WIDTHS:
        for scheme in SCHEMES:
            where = "%s at %d %s" % (name, width, scheme)
            page = ctx.new_page()
            try:
                page.set_viewport_size({"width": width, "height": 800})
                page.emulate_media(color_scheme=scheme)
                resp = page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                assert resp.status == 200, "%s answered %s" % (where, resp.status)
                page.wait_for_timeout(500)
                problems += _problems(page, where)
                problems += _arrows_reach_every_tab(page, where)
                problems += _choose_last_tab(page, where)
            finally:
                page.close()
    assert not problems, "\n".join(problems[:12])


def test_the_last_section_link_lands_on_a_page_with_content(signed_in, live_server, seeded):
    """My applications' sections are links: the last one opens a page that shows its own tab open and has content."""
    ctx = signed_in("application_manager")
    page = ctx.new_page()
    try:
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(live_server + "/my-applications/health", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Roadmap", exact=True).click()
        page.wait_for_url("**/my-applications/roadmap", timeout=PAGE_TIMEOUT)
        assert page.locator("main, [role=main], #main-content").first.inner_text().strip(), "the Roadmap page is empty"
        open_tab = page.locator("[data-tab-strip] [aria-current=page]")
        assert open_tab.inner_text().strip() == "Roadmap"
        assert page.evaluate("""() => { const t = document.querySelector('[data-tab-strip] [aria-current=page]');
          const b = t.closest('[data-tab-strip]').getBoundingClientRect(), r = t.getBoundingClientRect();
          return r.left >= b.left - 1 && r.right <= b.right + 1; }"""), "the open Roadmap tab is not in view"
    finally:
        page.close()


LOOK = """() => {
  const sel = '[role=tab], [data-slot=tabs-trigger]';
  const open = t => t.getAttribute('aria-selected') === 'true' || t.getAttribute('aria-current') === 'page' ||
                    t.getAttribute('data-active') === 'true';
  const strips = Array.from(document.querySelectorAll('[role=tablist], [data-tab-strip]'))
    .filter(s => s.offsetParent !== null && s.querySelectorAll(sel).length > 1)
    .filter((s, i, all) => !all.some(o => o !== s && o.contains(s)));
  return strips.map(s => {
    const tabs = Array.from(s.querySelectorAll(sel)).filter(t => t.offsetParent !== null);
    const look = t => { const c = getComputedStyle(t); return c.backgroundColor + ' / ' + c.color; };
    const shown = tabs.filter(open), closed = tabs.filter(t => !open(t));
    return {label: tabs.map(t => t.textContent.trim()).join(' | ').slice(0, 60),
            open: shown.length ? look(shown[0]) : null, closed: closed.map(look),
            controls: tabs.map(t => t.getAttribute('aria-controls')).filter(Boolean)
              .filter(id => !document.getElementById(id))};
  });
}"""


@pytest.mark.parametrize("name,path,role", ROUTES, ids=[r[0] for r in ROUTES])
def test_the_open_tab_looks_open_and_every_tab_controls_a_panel(signed_in, live_server, seeded, name, path, role):
    """A person can tell which tab is open: its background or text colour differs from every closed tab's."""
    url = live_server + path.format(application=seeded["ids"]["application"], solution=seeded["ids"]["solution"])
    problems = []
    ctx = signed_in(role)
    for width in (1440, 390):
        for scheme in SCHEMES:
            where = "%s at %d %s" % (name, width, scheme)
            page = ctx.new_page()
            try:
                page.set_viewport_size({"width": width, "height": 900})
                page.emulate_media(color_scheme=scheme)
                resp = page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                assert resp.status == 200, "%s answered %s" % (where, resp.status)
                page.wait_for_timeout(500)
                for strip in page.evaluate(LOOK):
                    if strip["open"] is None or not strip["closed"]:
                        continue
                    if any(c == strip["open"] for c in strip["closed"]):
                        problems.append("%s, strip '%s': the open tab looks like a closed one (%s)"
                                        % (where, strip["label"], strip["open"]))
                    if strip["controls"]:
                        problems.append("%s, strip '%s': aria-controls names no element: %s"
                                        % (where, strip["label"], strip["controls"]))
            finally:
                page.close()
    assert not problems, "\n".join(problems[:12])


@pytest.mark.parametrize("name,path,role", [r for r in ROUTES if r[0] in ("governance-dashboard", "rationalization-planning")],
                         ids=["governance-dashboard", "rationalization-planning"])
def test_a_focused_tab_is_not_under_the_edge_fade(signed_in, live_server, seeded, name, path, role):
    """Tabbing or arrowing to a tab brings it, and its focus ring, clear of the faded edge."""
    ctx = signed_in(role)
    page = ctx.new_page()
    try:
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(live_server + path.format(application=seeded["ids"]["application"]), wait_until="networkidle",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        problems = []
        count = page.evaluate("() => document.querySelectorAll('[data-tab-strip] [role=tab]').length")
        assert count > 1, "%s: no panel tab strip" % name
        for i in range(count):
            hidden = page.evaluate("""(i) => {
              const t = document.querySelectorAll('[data-tab-strip] [role=tab]')[i], s = t.closest('[data-tab-strip]');
              t.focus();
              return new Promise(res => setTimeout(() => {
                const b = s.getBoundingClientRect(), r = t.getBoundingClientRect(), f = s.getAttribute('data-fade');
                const end = f === 'end' || f === 'both', start = f === 'start' || f === 'both';
                res((end && r.right > b.right - 31) || (start && r.left < b.left + 31) ? t.textContent.trim() : null);
              }, 150));
            }""", i)
            if hidden:
                problems.append("tab '%s' sits under the faded edge when focused" % hidden)
        assert not problems, "\n".join(problems)
    finally:
        page.close()
