"""The most connected demo element's neighbourhood opens fitted, legible and in the theme, on a desktop and a phone.

The person: the demo company's enterprise architect, who follows "what does this connect to" from
the most connected element and lands on the Composer's focus view of its neighbourhood.
The goal: see every element of the neighbourhood clear of the card and the minimap, read every
connection word, and have no word sit on a box, a badge or another word, in either theme.
"""
import os

import pytest

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
VIEWS = [(1440, 900), (390, 844)]
SCHEMES = ["light", "dark"]
APP = '[x-data^="composerApp"]'


persona = {}


@pytest.fixture(scope="module")
def ledger(live_server, app):
    from sqlalchemy import text

    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            row = db.session.execute(text(
                "select e.id from archimate_elements e join organizations o on o.id = e.organization_id "
                "where o.slug = 'lantern-quay' "
                "order by (select count(*) from archimate_relationships r where r.source_id = e.id or r.target_id = e.id) desc, e.id limit 1")).first()
            assert row, "the demo company has no elements"
            from app.models.user import User
            from app.utils.role_access import is_admin
            user = User.query.filter_by(email=DEMO_EMAIL).one()
            persona["role"] = user.enterprise_role
            persona["admin"] = bool(is_admin(user))
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return row[0]


MEASURE = """() => {
  const box = (n) => { const r = n.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const shown = (sel) => { const n = document.querySelector(sel);
    return n && n.getClientRects().length ? box(n) : null; };
  const a = Alpine.$data(document.querySelector('[x-data^="composerApp"]'));
  const nodes = Array.from(document.querySelectorAll('#composer-canvas .joint-element')).map(box);
  const labels = Array.from(document.querySelectorAll('#composer-canvas .joint-link g.label'))
      .map(box).filter(b => b.w > 0 && b.h > 0);
  const badges = Array.from(document.querySelectorAll('#composer-canvas .joint-link g.label'))
      .length;
  return {
    canvas: box(document.querySelector('#composer-canvas')),
    card: shown('#composer-focus-banner'),
    minimap: shown('.mini-map'),
    nodes, labels, badges, zoom: a.zoomPercent,
    texts: Array.from(document.querySelectorAll('#composer-canvas .joint-element')).map(el => {
      const n = el.querySelector('[joint-selector="nameLabel"]'), t = el.querySelector('[joint-selector="typeLabel"]');
      if (!n || !t || !t.textContent.trim()) return {clash: false};
      const a = n.getBoundingClientRect(), b = t.getBoundingClientRect();
      return {clash: a.bottom > b.top + 1 && a.top < b.bottom - 1 && a.right > b.left && a.left < b.right, name: n.textContent};
    }),
    fills: Array.from(document.querySelectorAll('#composer-canvas .joint-link g.label rect')).map(r => {
      const css = getComputedStyle(r).fill, n = (css.match(/[\\d.]+/g) || [255, 255, 255]).map(Number);
      return css.startsWith('color(') ? n.slice(-3).map(v => Math.round(v * 255)) : n.slice(0, 3);
    }),
    ids: a.focusView ? a.focusView.ids.length : 0,
  };
}"""


def _hit(a, b, pad=0):
    return a["x"] < b["x"] + b["w"] + pad and a["x"] + a["w"] + pad > b["x"] \
        and a["y"] < b["y"] + b["h"] + pad and a["y"] + a["h"] + pad > b["y"]


def _open(browser, live_server, ledger, width, height, scheme):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", DEMO_EMAIL)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    page.goto("%s/archimate/composer?element=%d&depth=3&derived=1" % (live_server, ledger),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        """(sel) => { if (!window.Alpine || !document.querySelector(sel)) return false;
            const a = Alpine.$data(document.querySelector(sel));
            return !!a.focusView && a.graph.getElements().length > 1; }""", arg=APP, timeout=20000)
    page.wait_for_timeout(3500)
    return context, page


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("width,height", VIEWS)
def test_ledger_neighbourhood_is_fitted_clear_and_legible(browser, live_server, ledger, width, height, scheme):
    context, page = _open(browser, live_server, ledger, width, height, scheme)
    try:
        walk = os.environ.get("COMPOSER_WALK_DIR")
        if walk:
            os.makedirs(walk, exist_ok=True)
            page.screenshot(path="%s/ledger-%d-%s.png" % (walk, width, scheme))
        m = page.evaluate(MEASURE)
        where = "%dx%d %s" % (width, height, scheme)
        c = m["canvas"]
        assert persona["role"] == "enterprise_architect" and not persona["admin"], \
            "the journey must run as the Enterprise Architect, not an administrator: %r" % persona
        if width >= 1000:
            assert page.locator("header").get_by_text("Enterprise Architect").first.is_visible()
        assert len(m["nodes"]) >= 8 and len(m["nodes"]) == m["ids"], \
            "%s: %d of %d elements drawn" % (where, len(m["nodes"]), m["ids"])
        assert m["zoom"] > 10, "%s: zoom %s%%" % (where, m["zoom"])
        outside = [n for n in m["nodes"] if n["x"] < c["x"] - 1 or n["y"] < c["y"] - 1
                   or n["x"] + n["w"] > c["x"] + c["w"] + 1 or n["y"] + n["h"] > c["y"] + c["h"] + 1]
        assert not outside, "%s: %d of %d elements outside the canvas" % (where, len(outside), len(m["nodes"]))
        for name in ("card", "minimap"):
            cover = m[name]
            if cover:
                under = [n for n in m["nodes"] if _hit(n, cover)]
                assert not under, "%s: %d elements sit under the %s" % (where, len(under), name)
        labels = m["labels"]
        assert len(labels) >= 8, "%s: only %d connection words drawn" % (where, len(labels))
        cut = [b for b in labels if b["x"] < c["x"] - 1 or b["x"] + b["w"] > c["x"] + c["w"] + 1
               or b["y"] < c["y"] - 1 or b["y"] + b["h"] > c["y"] + c["h"] + 1]
        assert not cut, "%s: %d connection words are cut off by the canvas edge" % (where, len(cut))
        overlaps = [(i, j) for i, a in enumerate(labels) for j, b in enumerate(labels) if i < j and _hit(a, b)]
        assert not overlaps, "%s: %d connection words overlap each other" % (where, len(overlaps))
        on_nodes = [i for i, a in enumerate(labels) for n in m["nodes"] if _hit(a, n)]
        assert not on_nodes, "%s: %d connection words sit on an element" % (where, len(set(on_nodes)))
        crowded = [t for t in m["texts"] if t["clash"]]
        assert not crowded, "%s: type captions run through names: %r" % (where, crowded[:3])
        if scheme == "dark":
            light = [f for f in m["fills"] if sum(f) > 3 * 128]
            assert not light, "%s: %d label boxes read light on the dark canvas: %r" % (where, len(light), light[:2])
    finally:
        context.close()


def test_leaving_the_neighbourhood_for_another_composer_address_fires_no_prompt(browser, live_server, ledger):
    context, page = _open(browser, live_server, ledger, 1440, 900, "light")
    try:
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
        page.mouse.click(700, 500)
        page.goto("%s/archimate/composer?element=%d&depth=1" % (live_server, ledger),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert dialogs == [], "moving between two Composer addresses prompted: %r" % dialogs
        page.wait_for_function(
            """(sel) => { const a = Alpine.$data(document.querySelector(sel));
                return !!a.focusView && a.graph.getElements().length > 1; }""", arg=APP, timeout=15000)
    finally:
        context.close()


def test_the_fit_follows_the_window_when_it_is_resized(browser, live_server, ledger):
    context, page = _open(browser, live_server, ledger, 1440, 900, "light")
    try:
        page.wait_for_timeout(3500)  # well past the first settling seconds
        page.set_viewport_size({"width": 1100, "height": 760})
        page.wait_for_timeout(800)
        m = page.evaluate(MEASURE)
        c = m["canvas"]
        outside = [n for n in m["nodes"] if n["x"] < c["x"] - 1 or n["y"] < c["y"] - 1
                   or n["x"] + n["w"] > c["x"] + c["w"] + 1 or n["y"] + n["h"] > c["y"] + c["h"] + 1]
        assert not outside, "%d of %d elements outside the canvas after a resize" % (len(outside), len(m["nodes"]))
        if m["card"]:
            assert not [n for n in m["nodes"] if _hit(n, m["card"])], "elements under the card after a resize"
    finally:
        context.close()
