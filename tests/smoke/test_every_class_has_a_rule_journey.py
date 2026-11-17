"""Every class name on a signed-in page has a rule in a stylesheet that page loads.

A platform administrator in an organisation holding linked records opens the Overview, the
value streams list and one value stream, the ARB sessions, the AI chat, the applications and the
team, at a desktop and a phone width, in the light and the dark theme. On each page the check
lists every class name on any element that no loaded stylesheet has a rule for. A class with no
rule does nothing: a card header holding an action is not laid out in two columns, an invalid
field has no red ring, a disabled menu item looks like an enabled one.
"""
import json
import re
from pathlib import Path

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

COLLECT = r"""
() => {
  // Where a class is named in a selector: the compound it sits in ("a.b" in "p > a.b:hover"),
  // with the parts that depend on a state (pseudo-classes, pseudo-elements, attribute tests,
  // the dark-theme ancestor) dropped, so that "can this rule ever reach the element carrying the
  // class" is answered by element.matches() rather than by the class appearing in a sheet.
  const drop = (text) => {
    let out = '';
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === '\\') { out += ch + (text[i + 1] || ''); i++; continue; }
      if (ch === '[') {
        let j = i; while (j < text.length && text[j] !== ']') j++;
        i = j; continue;
      }
      if (ch === ':') {
        let j = i + 1; if (text[j] === ':') j++;
        while (j < text.length && /[\w-]/.test(text[j])) j++;
        const name = text.slice(i, j);
        const keep = name === ':is' || name === ':where';
        if (text[j] === '(') {
          let depth = 0, k = j;
          for (; k < text.length; k++) {
            if (text[k] === '(') depth++;
            if (text[k] === ')' && --depth === 0) break;
          }
          if (keep) out += text.slice(i, k + 1);
          i = k; continue;
        }
        i = j - 1; continue;
      }
      out += ch;
    }
    return out;
  };
  const compounds = (selector) => {
    const found = [];
    let depth = 0, cur = '';
    const flush = () => { if (cur.trim()) found.push(cur.trim()); cur = ''; };
    for (let i = 0; i < selector.length; i++) {
      const ch = selector[i];
      if (ch === '\\') { cur += ch + (selector[i + 1] || ''); i++; continue; }
      if (ch === '(' || ch === '[') depth++;
      if (ch === ')' || ch === ']') depth--;
      if (depth === 0 && (ch === ' ' || ch === '>' || ch === '+' || ch === '~')) flush();
      else cur += ch;
    }
    flush();
    return found;
  };
  const splitTop = (selector) => {
    const parts = []; let depth = 0, cur = '';
    for (let i = 0; i < selector.length; i++) {
      const ch = selector[i];
      if (ch === '\\') { cur += ch + (selector[i + 1] || ''); i++; continue; }
      if (ch === '(' || ch === '[') depth++;
      if (ch === ')' || ch === ']') depth--;
      if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; } else cur += ch;
    }
    parts.push(cur);
    return parts;
  };
  const classesOf = (compound) => {
    const names = [];
    for (const m of compound.matchAll(/\.((?:\\.|[\w-])+)/g)) names.push(m[1].replace(/\\(.)/g, '$1'));
    return names;
  };
  const named = new Map();
  const styles = new Set();
  const walk = (rules) => {
    for (const r of rules) {
      if (r.selectorText) {
        for (const one of splitTop(r.selectorText)) {
          const parts = compounds(drop(one.replace(/\.dark /g, '')));
          parts.forEach((compound, index) => {
            for (const c of classesOf(compound)) {
              if (!named.has(c)) named.set(c, new Set());
              // The last compound is the element the rule styles: a rule limited to one tag
              // or state there ("a" in ".x:is(a)") is a condition on that element, not a
              // rule that cannot reach it. An earlier compound is an ancestor or sibling the
              // class has to sit on, and that is what element.matches() has to confirm.
              if (index === parts.length - 1) styles.add(c);
              else named.get(c).add(compound);
            }
          });
        }
      }
      if (r.cssRules) walk(r.cssRules);
    }
  };
  let unreadable = 0;
  for (const sheet of document.styleSheets) {
    try { walk(sheet.cssRules); } catch (e) { unreadable += 1; }
  }
  const missing = {};
  const unreached = {};
  const carriers = {};
  for (const el of document.querySelectorAll('body *')) {
    if (!(el.classList && el.classList.length)) continue;
    for (const c of el.classList) (carriers[c] = carriers[c] || []).push(el);
  }
  for (const [c, els] of Object.entries(carriers)) {
    if (!named.has(c)) { missing[c] = els.length; continue; }
    const reaches = styles.has(c) || [...named.get(c)].some((compound) => els.some((el) => {
      try { return el.matches(compound); } catch (e) { return true; }
    }));
    if (!reaches) unreached[c] = els.length;
  }
  return {missing, unreached, unreadable};
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
    unreached = {}
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
        never = sorted(c for c in found["unreached"] if not MARKERS.match(c))
        if never:
            unreached[title] = never
    assert not unstyled, "classes with no rule in a loaded stylesheet (%s, %s):\n%s" % (
        size[0], theme, "\n".join("  %s (%d): %s" % (t, len(n), ", ".join(n)) for t, n in unstyled.items()))
    assert not unreached, "classes whose rules cannot match the element that carries them (%s, %s):\n%s" % (
        size[0], theme, "\n".join("  %s (%d): %s" % (t, len(n), ", ".join(n)) for t, n in unreached.items()))


def test_an_image_in_an_attachment_fills_its_frame(live_server, pages):
    """The attachment's image variant styles the pictures inside it, not the picture's children.

    The class list is the one the component recipes carry; it is put on a frame holding a
    picture on the Overview, and the picture's own computed style is read.
    """
    page, paths = pages
    recipes = json.loads((Path(__file__).resolve().parents[2] / "app/basecoat_kit/data/recipes.json").read_text())
    classes = recipes["attachment-media"]["variants"]["variants"]["variant"]["image"]
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server + paths["Overview"], wait_until="networkidle", timeout=PAGE_TIMEOUT)
    style = page.evaluate(
        """(classes) => {
          const frame = document.createElement('div');
          frame.className = classes;
          frame.style.width = '120px';
          const picture = document.createElement('img');
          picture.alt = '';
          picture.src = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
          frame.appendChild(picture);
          document.body.appendChild(frame);
          const css = getComputedStyle(picture);
          const out = {fit: css.objectFit, width: picture.getBoundingClientRect().width, frame: frame.getBoundingClientRect().width};
          frame.remove();
          return out;
        }""",
        classes,
    )
    assert style["fit"] == "cover", "the picture in an attachment frame is not cropped to fill it: %s" % style
    assert style["width"] == style["frame"], "the picture does not fill the width of its frame: %s" % style
