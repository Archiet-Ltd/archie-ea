"""A new user's first ten minutes, read in both themes and at both widths.

The path a new account walks before its first answer is registration, the
first-run dialog, the dashboard, the import screen, Ask and the twin map. This
file drives that path the way a user does -- it registers a brand-new account
through the form rather than seeding one -- and on every screen measures the
contrast of each piece of visible text against the background actually painted
behind it.

Why contrast, measured in the browser: the defect this guards against was text
that carries no colour class of its own and inherits whatever the page body
gives it. The compiled stylesheet gave the body no text colour at all, so in the
dark theme that text fell back to the browser default, black, on a near-black
card -- the product name at the top of every sidebar measured about 1.05:1.
Every template involved was valid and every token gate was green; only a
rendered measurement can see the difference between "inherits a token" and
"inherits nothing".

Set SMOKE_SCREENSHOT_DIR to keep a full-page capture of every screen visited.
"""

import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "FirstRun!Journey2026"

# WCAG 2.2 AA: 4.5:1 for body text, 3:1 for large text (24px, or 18.66px bold).
MEASURE_CONTRAST = r"""() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const lum = ({r, g, b}) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  // The colour painted behind an element: the nearest ancestor with an opaque
  // background. Gradients and images are skipped, since their colour cannot be
  // read from style, and so are elements with no measurable backdrop.
  const backdrop = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const c = parse(s.backgroundColor);
      if (c && c.a >= 0.95) return c;
    }
    return parse(getComputedStyle(document.documentElement).backgroundColor);
  };
  const found = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION'].includes(el.tagName)) continue;
    const text = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
    if (!text) continue;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    // Visually hidden (sr-only skip links clip to a 1px box) is not on screen.
    if (r.width <= 2 || r.height <= 2 || s.visibility === 'hidden' || s.display === 'none') continue;
    if (parseFloat(s.opacity) < 0.95) continue;
    // Hidden by an ancestor (collapsed panel, closed menu, off-canvas drawer).
    if (!el.checkVisibility || !el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    // Parked off-screen until focused (the skip links).
    if (r.right <= 0 || r.left >= window.innerWidth || r.bottom <= 0) continue;
    const fg = parse(s.color);
    const bg = backdrop(el);
    if (!fg || !bg || fg.a < 0.95) continue;
    const size = parseFloat(s.fontSize);
    const bold = parseInt(s.fontWeight, 10) >= 700;
    const large = size >= 24 || (bold && size >= 18.66);
    const need = large ? 3 : 4.5;
    const got = ratio(fg, bg);
    // Placeholder-like disabled controls are exempt under WCAG 1.4.3.
    if (el.closest('[disabled],[aria-disabled="true"]')) continue;
    if (got < need) {
      const key = text.slice(0, 40);
      if (seen.has(key)) continue;
      seen.add(key);
      found.push({text: key, ratio: Math.round(got * 100) / 100, need,
                  color: s.color, tag: el.tagName.toLowerCase()});
    }
  }
  return found;
}"""


def _capture(page, name):
    target = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if target:
        os.makedirs(target, exist_ok=True)
        page.screenshot(path=os.path.join(target, name + ".png"), full_page=True)


def _set_theme(page, theme):
    """Apply a theme the way the user-menu switch does: store it, then reload."""
    page.evaluate(
        "(t) => { try { localStorage.setItem('theme', t); } catch (e) {} "
        "document.documentElement.classList.toggle('dark', t === 'dark'); }",
        theme,
    )


def _open(page, base, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)


def _unreadable(page):
    return page.evaluate(MEASURE_CONTRAST)


def _register(page, base):
    """Create a brand-new account through the form, as a first-time user does."""
    email = f"first.run.{uuid.uuid4().hex[:10]}@gmail.com"
    _open(page, base, "/account/register")
    page.fill("#first_name", "First")
    page.fill("#last_name", "Runner")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    return email


# The screens between sign-up and the first answer, in the order they are met.
SIGNED_IN_PATH = [
    ("dashboard", "/dashboard/overview"),
    ("import", "/architecture/import/oef"),
    ("ask", "/intelligence/ask"),
    ("twin-map", "/intelligence/twin-map"),
]


@pytest.mark.parametrize("page", [1440, 390], indirect=True, ids=["desktop", "phone"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_first_run_path_text_is_readable(page, live_server, theme):
    width = page.viewport_size["width"]
    tag = f"{width}-{theme}"
    problems = {}

    # Registration, signed out.
    _open(page, live_server, "/account/register")
    _set_theme(page, theme)
    _open(page, live_server, "/account/register")
    _capture(page, f"register-{tag}")
    if bad := _unreadable(page):
        problems["/account/register"] = bad

    _register(page, live_server)
    _set_theme(page, theme)
    _open(page, live_server, "/dashboard/overview")

    # The first-run dialog is what a new account sees first; read it before
    # it is dismissed.
    dialog = page.locator("[x-show='showOnboarding']").first
    assert dialog.count() and dialog.is_visible(), (
        "a brand-new account should be shown the first-run dialog on its first page"
    )
    _capture(page, f"onboarding-{tag}")
    if bad := _unreadable(page):
        problems["first-run dialog"] = bad
    page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")

    for name, path in SIGNED_IN_PATH:
        _open(page, live_server, path)
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
        _capture(page, f"{name}-{tag}")
        if bad := _unreadable(page):
            problems[path] = bad

    assert not problems, (
        f"text below the WCAG AA contrast minimum on the new-user path ({tag}):\n"
        + "\n".join(f"  {where}: {items}" for where, items in problems.items())
    )


def test_sidebar_product_name_is_readable_in_dark_theme(page, live_server, seeded):
    """The product name beside the logo is on every signed-in page. It carried
    no colour of its own and inherited none, so the dark theme drew it black on
    the dark sidebar."""
    from .test_archetype_journeys import _login, _visit

    _login(page, live_server, seeded["emails"]["solution_architect"])
    _set_theme(page, "dark")
    _visit(page, live_server, "/dashboard/overview")
    name = page.locator(".sidebar-app-name").first
    assert name.is_visible()
    color = name.evaluate("el => getComputedStyle(el).color")
    body = page.evaluate("() => getComputedStyle(document.body).color")
    assert color != "rgb(0, 0, 0)", f"sidebar product name is black in the dark theme ({color})"
    assert body != "rgb(0, 0, 0)", f"page body text colour is black in the dark theme ({body})"
