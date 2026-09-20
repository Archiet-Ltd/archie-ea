"""A person asks a question, reads the answer, asks why, and sees the map: in a real browser.

This drives the Ask page, the provenance drawer and the Twin map exactly as a
person does, against a tenant that holds a small chain of connected things:

    Service --serves--> Gateway --serves--> Ops Team
    Service --serves--> Portal
    Service ==worked out==> Ops Team          (a connection nobody drew)

The Gateway has an owner; the Portal and the Ops Team have none. Nothing is
asked of the person before the answer: no layer, no type, no vocabulary term.

What each test proves is stated in its docstring. Every assertion is made on the
rendered page; where a test compares the page with the answer the server gave, it
asks the server itself with the same session, so the two cannot drift.
"""

import json
import os
import re
import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .intelligence_graph import mark_derived_stale, seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MIN_TARGET = 24


# --- the tenant the journey reads -------------------------------------------


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    """A chain of connected elements in the seeded organisation, with one owner
    and one worked-out connection."""
    return seed_impact_graph(seeded["ids"]["org"])


@pytest.fixture
def bare_tenant(live_server):
    """A fresh organisation with two connected elements, no owner and no worked-out
    connections, for the states that depend on something being absent. One per test:
    asking for the connections to be worked out changes the tenant it runs in."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"noun": "Nightshift %s" % suffix}
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Bare Org %s" % suffix, slug="bare-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="bare.%s@example.com" % suffix, first_name="Bare", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id)
            db.session.add(row)
            db.session.commit()
            return row

        head = element("%s Batch" % out["noun"], "ApplicationComponent", "application")
        tail = element("%s Report" % out["noun"], "ApplicationComponent", "application")
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=head.id, target_id=tail.id, organization_id=org.id,
        ))
        db.session.commit()
        out.update(email=user.email, head=head.id, tail=tail.id, org=org.id,
                   head_name=head.name, tail_name=tail.name)
    return out


@pytest.fixture(scope="module")
def empty_tenant(live_server):
    """An organisation that holds nothing at all, with a user who has finished
    the first-run prompt."""
    import datetime

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        # The vendor count is global by construction, so a database that holds
        # any vendor organisation has no tenant that can read as empty.
        if db.session.query(db.func.count(VendorOrganization.id)).scalar():
            pytest.skip("vendor organisations exist in this database, so no tenant reads as empty")
        Role.insert_roles()
        org = Organization(name="Empty Org %s" % suffix, slug="empty-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="empty.%s@example.com" % suffix, first_name="Empty", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            onboarding_completed_at=datetime.datetime.utcnow(),
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return {"email": user.email}


# --- driving the page -------------------------------------------------------


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _ready(page, factory):
    """The page's component has started, so a click will do something."""
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _open_ask(page, base):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")


def _open_twin_map(page, base, element_id=None):
    url = base + "/intelligence/twin-map" + ("?element=%s" % element_id if element_id else "")
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "twinMapSurface")


def _wait_for_map(page):
    """The map is drawn: its connections exist (a perfectly vertical line has no width, so
    it is attached rather than "visible" to the browser) and its elements can be seen."""
    page.wait_for_selector("svg .intel-edge", state="attached")
    page.wait_for_selector("[data-graph-nodes] button", state="visible")


def _settled(page, selector):
    """The element has slid fully into the window: its right edge is inside the viewport."""
    page.wait_for_function(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.left >= 0 && r.right <= innerWidth + 0.5;
        }""",
        arg=selector,
    )


def _dismiss_first_run(page):
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass


def _type_and_wait(page, prefix, term):
    box = page.locator("#%s-picker-input" % prefix)
    box.press_sequentially(term, delay=15)
    page.wait_for_selector("#%s-picker-listbox [role=option]" % prefix)
    return box


def _pick_by_click(page, prefix, name):
    page.locator("#%s-picker-listbox [role=option]" % prefix, has_text=name).click()


def _rows(page):
    return page.locator("[data-ask-row]")


def _row_for(page, name, kind=None):
    rows = page.locator("[data-ask-row]" + ('[data-kind="%s"]' % kind if kind else ""))
    return rows.filter(has=page.get_by_role("heading", name=name, exact=True))


def _impact_api(page, base, element_id, **params):
    query = {"include_derived": "true", "max_depth": "3", "with_owner": "true"}
    query.update({k: str(v) for k, v in params.items()})
    response = page.context.request.get(
        "%s/api/v1/intelligence/impact/%s?%s" % (
            base, element_id, "&".join("%s=%s" % kv for kv in query.items()))
    )
    assert response.status == 200, response.text()
    return response.json()["data"]


def _ask_about(page, base, graph):
    """The whole first half of the journey with the mouse: sidebar, card, typing, choosing."""
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    page.get_by_test_id("sidebar").get_by_role("link", name="Ask a question").click()
    page.wait_for_url(re.compile(r"/intelligence/ask$"))
    _ready(page, "askSurface")
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_selector("[data-ask-row]")


def _focus_is_on(page, locator):
    return page.evaluate(
        "(el) => document.activeElement === el", locator.element_handle()
    )


# --- the journey with a pointer ---------------------------


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect"])
def test_a_first_time_user_gets_an_answer_from_a_typed_noun_and_can_ask_why(
    persona, page, live_server, seeded, graph
):
    """From the sidebar to the map, as two personas, having declared nothing.

    Open Ask from the sidebar, open the question, type a noun, choose a match,
    read the rows (an owner where there is one, "Not recorded" where there is not),
    open the drawer from a connection nobody drew, read the sentence the server
    wrote, expand Full detail, follow View on twin map, move the hop depth, turn
    the worked-out connections off and on, and see the derived edge dashed.
    """
    _login(page, live_server, seeded["emails"][persona])
    _ask_about(page, live_server, graph)

    # Nothing was asked for: one text input, no layer or type control anywhere.
    assert page.locator("main select").count() == 0
    assert page.locator("main input[type=text], main input[role=combobox]").count() == 1
    names = graph["names"]
    assert _rows(page).count() == 4

    # An owner where there is one; a stated reason where there is not.
    gateway = _row_for(page, names["gateway"], "explicit")
    assert gateway.get_by_label("Owner: " + graph["owner"]).count() == 1
    portal = _row_for(page, names["portal"], "explicit")
    assert portal.get_by_label("Owner: Not recorded").count() == 1
    assert "Not recorded" in portal.inner_text()

    # The connection nobody drew says so, and asks nobody to trust it blindly.
    derived = _row_for(page, names["ops"], "derived")
    assert derived.count() == 1
    assert "We worked this out — nobody drew it directly." in derived.inner_text()

    # Why? opens the drawer with the server's own sentence, verbatim.
    derived.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    served = [
        row["relation"]["plain_terms"]
        for row in _impact_api(page, live_server, graph["service"])["rows"]
        if row["relation"]["kind"] == "derived"
    ]
    assert len(served) == 1 and served[0]
    sentence = dialog.locator("[data-plain-terms]")
    assert sentence.inner_text() == served[0]
    assert names["ops"] in served[0] and names["service"] in served[0]

    # Full detail is collapsed, opens in place, and holds the technical terms.
    toggle = dialog.locator("[data-full-detail-toggle]")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    url_before = page.url
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    assert page.url == url_before
    assert len(page.context.pages) == 1
    detail = dialog.locator("[data-full-detail-region]")
    text = detail.inner_text()
    for expected in ("BusinessActor", "business", "serving-through-serving", "0.82", "1.0"):
        assert expected in text, (expected, text)
    for name in (names["service"], names["gateway"], names["ops"]):
        assert name in text  # the chain, as names

    # Close, then follow View on twin map from a row.
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    _row_for(page, names["gateway"], "explicit").get_by_role("link", name="View on twin map").click()
    page.wait_for_url(re.compile(r"/intelligence/twin-map\?element=%s$" % graph["gateway"]))
    _ready(page, "twinMapSurface")
    _wait_for_map(page)
    assert page.locator("[data-map-row]").count() >= 1

    # The map centred on the Service, then hop depth and the worked-out toggle.
    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    assert page.locator("svg path[data-kind=derived]").count() == 1
    assert page.locator("svg path[data-kind=explicit]").count() == 3
    assert page.locator("[data-map-row]").count() == 4

    slider = page.locator("#twin-hop-depth")
    slider.focus()
    with page.expect_request(lambda r: "max_depth=1" in r.url and "/impact/" in r.url):
        page.keyboard.press("Home")
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 2")
    assert page.locator("svg path[data-kind=derived]").count() == 0
    with page.expect_request(lambda r: "max_depth=5" in r.url and "/impact/" in r.url):
        page.keyboard.press("End")
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 4")

    toggle_box = page.locator("#twin-show-derived")
    with page.expect_request(lambda r: "include_derived=false" in r.url and "/impact/" in r.url):
        toggle_box.uncheck()
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 3")
    assert page.locator("svg path[data-kind=derived]").count() == 0
    with page.expect_request(lambda r: "include_derived=true" in r.url and "/impact/" in r.url):
        toggle_box.check()
    page.wait_for_selector("svg path[data-kind=derived]")


def test_the_derived_edge_is_dashed_and_badged_and_explicit_edges_are_solid(
    page, live_server, seeded, graph
):
    """Derived-versus-explicit survives greyscale: it rides the stroke pattern and a
    written badge, read off the rendered SVG."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_twin_map(page, live_server, graph["service"])
    page.wait_for_selector("svg path[data-kind=derived]")
    derived = page.locator("svg path[data-kind=derived]")
    assert derived.get_attribute("stroke-dasharray") in ("5,5", "5, 5")
    solid = page.locator("svg path[data-kind=explicit]")
    for index in range(solid.count()):
        assert solid.nth(index).get_attribute("stroke-dasharray") is None
    badges = page.locator("svg g.intel-badge")
    assert badges.count() == 1
    assert badges.locator("text").text_content() == "Worked out"
    # No colour of its own: every fill and stroke comes from a token class.
    assert page.locator("svg [style*='#'], svg [fill^='#'], svg [stroke^='#']").count() == 0


# --- keyboard and focus helpers ---------------------------------------------

RECORD_POINTER = """
(() => {
  const note = (type) => {
    try {
      const seen = JSON.parse(sessionStorage.getItem('__pointer') || '[]');
      seen.push(type);
      sessionStorage.setItem('__pointer', JSON.stringify(seen));
    } catch (e) { /* recording only */ }
  };
  ['pointerdown', 'mousedown', 'mouseup', 'touchstart'].forEach((type) =>
    window.addEventListener(type, () => note(type), true));
})();
"""

DESCRIBE_FOCUS = """
() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getBoundingClientRect();
  const cs = getComputedStyle(a);
  const ring = (cs.boxShadow && cs.boxShadow !== 'none') ||
    (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0);
  return {
    tag: a.tagName, id: a.id, role: a.getAttribute('role'),
    text: (a.innerText || a.value || '').trim().slice(0, 80),
    label: a.getAttribute('aria-label'),
    node: a.getAttribute('data-node'),
    fullDetail: a.hasAttribute('data-full-detail-toggle'),
    inTable: !!a.closest('table[data-map-table]'),
    inDialog: !!a.closest('#drawer-provenance [role=dialog]'),
    visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none',
    focusVisible: a.matches(':focus-visible'),
    ring: !!ring,
    inViewport: r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth,
  };
}
"""


def _focus(page):
    return page.evaluate(DESCRIBE_FOCUS)


def _assert_focus_is_visible(state, where):
    assert state is not None, "focus is on nothing at: %s" % where
    assert state["visible"], "focus landed on a hidden element at %s: %r" % (where, state)
    assert state["focusVisible"] and state["ring"], (
        "no visible focus indicator at %s: %r" % (where, state))


def _press(page, key, where):
    page.keyboard.press(key)
    if key in ("Tab", "Shift+Tab"):
        _assert_focus_is_visible(_focus(page), where)


def _tab_until(page, matches, where, limit=140):
    """Press Tab until the focused control satisfies `matches`, checking every stop."""
    for _ in range(limit):
        _press(page, "Tab", where)
        state = _focus(page)
        if matches(state):
            return state
    raise AssertionError("never reached %s by Tab in %d presses; last stop: %r" % (
        where, limit, _focus(page)))


def _measure(locator):
    box = locator.bounding_box()
    assert box is not None, "control is not rendered"
    return {"width": round(box["width"], 1), "height": round(box["height"], 1),
            "x": box["x"], "y": box["y"]}


# --- the picker is an ARIA 1.2 combobox ----------------------------


def test_the_picker_is_an_aria_combobox_and_works_from_the_keyboard(
    page, live_server, seeded, graph
):
    """The input is the combobox and keeps focus; the options are a listbox; the active
    option is tracked with aria-activedescendant; Escape closes and leaves the typed
    text; a polite status region says how many matches came back."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()

    box = page.locator("#ask-picker-input")
    assert box.get_attribute("role") == "combobox"
    assert box.get_attribute("aria-autocomplete") == "list"
    expect(box).to_have_attribute("aria-expanded", "false")
    listbox_id = box.get_attribute("aria-controls")
    listbox = page.locator("#" + listbox_id)
    assert listbox.get_attribute("role") == "listbox"
    assert box.get_attribute("aria-label") == "Type a team, service or system name"
    assert box.get_attribute("placeholder") == "Type a team, service or system name"

    box.press_sequentially(graph["noun"], delay=15)
    page.wait_for_selector("#%s [role=option]" % listbox_id)
    expect(box).to_have_attribute("aria-expanded", "true")
    options = listbox.locator("[role=option]")
    assert options.count() == 4
    ids = [options.nth(i).get_attribute("id") for i in range(4)]
    assert all(ids) and len(set(ids)) == 4

    status = page.locator("#ask-question-panel-impact [role=status]")
    assert status.inner_text() == "4 results for %s" % graph["noun"]
    assert status.get_attribute("aria-live") == "polite"
    assert status.get_attribute("aria-atomic") == "true"

    def active():
        return box.get_attribute("aria-activedescendant")

    assert not active()
    page.keyboard.press("ArrowDown")
    assert active() == ids[0]
    page.keyboard.press("ArrowDown")
    assert active() == ids[1]
    page.keyboard.press("ArrowUp")
    assert active() == ids[0]
    page.keyboard.press("End")
    assert active() == ids[3]
    page.keyboard.press("Home")
    assert active() == ids[0]
    assert _focus_is_on(page, box), "DOM focus left the input"
    assert options.nth(0).get_attribute("aria-selected") == "true"

    page.keyboard.press("Escape")
    expect(box).to_have_attribute("aria-expanded", "false")
    expect(listbox).to_be_hidden()
    assert box.input_value() == graph["noun"]
    assert _focus_is_on(page, box)

    # Enter on the active option chooses it.
    page.keyboard.press("ArrowDown")
    expect(box).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-ask-row]")
    expect(box).to_have_attribute("aria-expanded", "false")


# --- the whole journey by keyboard alone ---------------------------


def test_the_whole_journey_by_keyboard_alone(page, live_server, seeded, graph):
    """Nothing here is a pointer event: page loads, Tab, arrows, Home, End, Enter, Space,
    Escape and typing. Focus is visible at every stop and never on a hidden element, and
    a recorder in the page confirms no pointer event fired."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.add_init_script(RECORD_POINTER)
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")
    _dismiss_first_run(page)
    page.evaluate("sessionStorage.removeItem('__pointer')")

    # The skip link is the first stop and moves focus to the page content.
    _press(page, "Tab", "the skip link")
    assert _focus(page)["text"] == "Skip to main content"
    page.keyboard.press("Enter")
    _tab_until(page, lambda s: s["id"] == "ask-question-impact", "the question")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.activeElement.id === 'ask-picker-input'")
    _assert_focus_is_visible(_focus(page), "the picker")

    page.keyboard.type(graph["noun"], delay=25)
    page.wait_for_selector("#ask-picker-listbox [role=option]")
    wanted = graph["names"]["service"]
    for _ in range(10):
        page.keyboard.press("ArrowDown")
        active = page.locator("#ask-picker-input").get_attribute("aria-activedescendant")
        if page.locator("#" + active).inner_text().strip() == wanted:
            break
    else:
        raise AssertionError("ArrowDown never reached %r" % wanted)
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-ask-row]")

    # A row's Why? opens the drawer; focus moves into it.
    _tab_until(page, lambda s: s["text"] == "Why?", "a row's Why?")
    page.evaluate("window.__opener = document.activeElement")
    page.keyboard.press("Enter")
    page.locator("#drawer-provenance [role=dialog]").wait_for(state="visible")
    page.wait_for_function("() => !!document.activeElement.closest('#drawer-provenance [role=dialog]')")
    _assert_focus_is_visible(_focus(page), "the drawer")

    # Tab inside the drawer to Full detail and open it.
    _tab_until(page, lambda s: s["fullDetail"], "Full detail in the drawer", limit=12)
    page.keyboard.press("Enter")
    page.locator("#drawer-provenance [data-full-detail-region]").wait_for(state="visible")

    # Escape closes it and focus is back on the exact control that opened it.
    page.keyboard.press("Escape")
    page.locator("#drawer-provenance [role=dialog]").wait_for(state="hidden")
    page.wait_for_function("() => document.activeElement === window.__opener")
    _assert_focus_is_visible(_focus(page), "the opener after closing the drawer")

    # On to the Twin map through the row's link.
    _tab_until(page, lambda s: s["text"] == "View on twin map", "View on twin map")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/intelligence/twin-map\?element=\d+$"))
    _ready(page, "twinMapSurface")
    _wait_for_map(page)

    # Hop depth: arrows, Home and End, no pointer.
    _tab_until(page, lambda s: s["id"] == "twin-hop-depth", "the hop depth control")
    with page.expect_request(lambda r: "max_depth=1" in r.url and "/impact/" in r.url):
        page.keyboard.press("Home")
    assert page.locator("#twin-hop-depth").input_value() == "1"
    page.keyboard.press("ArrowRight")
    assert page.locator("#twin-hop-depth").input_value() == "2"
    with page.expect_request(lambda r: "max_depth=5" in r.url and "/impact/" in r.url):
        page.keyboard.press("End")
    assert page.locator("#twin-hop-depth").input_value() == "5"

    # The worked-out toggle is a checkbox: Space flips it and re-asks.
    _tab_until(page, lambda s: s["id"] == "twin-show-derived", "the worked-out toggle")
    with page.expect_request(lambda r: "include_derived=false" in r.url and "/impact/" in r.url):
        page.keyboard.press("Space")
    with page.expect_request(lambda r: "include_derived=true" in r.url and "/impact/" in r.url):
        page.keyboard.press("Space")

    # A map element is reachable and selectable, and so is the table.
    _tab_until(page, lambda s: bool(s["node"]), "a map element")
    page.keyboard.press("Enter")
    assert page.locator("button[data-node][aria-pressed=true]").count() == 1
    _tab_until(page, lambda s: s["inTable"], "the map table")
    assert page.evaluate("JSON.parse(sessionStorage.getItem('__pointer') || '[]')") == [], (
        "a pointer event fired during a keyboard-only journey")


# --- target size ---------------------------------------------------


def _record(measurements):
    folder = os.environ.get("T004B_EVIDENCE_DIR")
    print("[target-size] " + json.dumps(measurements, sort_keys=True))
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "target-size.json"), "w", encoding="utf-8") as fh:
            json.dump(measurements, fh, indent=2, sort_keys=True)


def test_every_control_is_at_least_24_by_24_css_pixels(page, live_server, seeded, graph):
    """Measured in the browser on the rendered controls, and recorded. Each one meets
    the size itself; none relies on the spacing exception."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    measurements = {}
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_selector("[data-ask-row]")
    row = _row_for(page, graph["names"]["gateway"], "explicit")
    measurements["Why? (Ask row)"] = _measure(row.get_by_role("button", name="Why?"))
    measurements["Full detail (Ask row)"] = _measure(row.locator("[data-full-detail-toggle]"))
    measurements["View on twin map"] = _measure(row.get_by_role("link", name="View on twin map"))
    row.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    measurements["Close drawer"] = _measure(dialog.get_by_role("button", name="Close drawer"))
    measurements["Full detail (drawer)"] = _measure(dialog.locator("[data-full-detail-toggle]"))
    page.keyboard.press("Escape")

    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    measurements["Hop depth slider"] = _measure(page.locator("#twin-hop-depth"))
    measurements["Worked-out toggle"] = _measure(page.locator("#twin-show-derived"))
    measurements["Selected element (rail)"] = _measure(page.locator("#twin-rail-toggle"))
    nodes = page.locator("[data-graph-nodes] button")
    for index in range(nodes.count()):
        measurements["Map element %d" % (index + 1)] = _measure(nodes.nth(index))
    first = page.locator("[data-map-row]").first
    measurements["Why? (table row)"] = _measure(first.get_by_role("button", name="Why?"))
    measurements["Centre on this"] = _measure(first.get_by_role("button", name="Centre on this"))
    measurements["Full detail (rail)"] = _measure(page.locator("#twin-rail [data-full-detail-toggle]"))
    _record(measurements)

    small = {name: m for name, m in measurements.items()
             if m["width"] < MIN_TARGET or m["height"] < MIN_TARGET}
    assert not small, "controls under %dx%d CSS pixels: %r" % (MIN_TARGET, MIN_TARGET, small)


# --- the slider needs no dragging ----------------------------------


def test_the_hop_depth_slider_works_without_dragging(page, live_server, seeded, graph):
    """A native range input: keys and a single click set it, it has a name, and the new
    value is announced politely."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    slider = page.get_by_role("slider", name="Hop depth")
    assert slider.count() == 1
    assert page.locator("#twin-hop-depth").evaluate("el => el.tagName + el.type") == "INPUTrange"
    live = page.locator("[role=status][aria-live=polite]", has_text=re.compile(r"^Hop depth: \d$"))

    slider.focus()
    page.keyboard.press("Home")
    assert slider.input_value() == "1"
    page.keyboard.press("ArrowRight")
    assert slider.input_value() == "2"
    page.keyboard.press("ArrowUp")
    assert slider.input_value() == "3"
    page.keyboard.press("ArrowLeft")
    assert slider.input_value() == "2"
    page.keyboard.press("ArrowDown")
    assert slider.input_value() == "1"
    page.keyboard.press("End")
    assert slider.input_value() == "5"
    assert live.count() == 1
    assert live.get_attribute("aria-atomic") == "true"
    assert live.inner_text() == "Hop depth: 5"

    # One click on the track, no drag: the value moves to where the click landed.
    box = slider.bounding_box()
    with page.expect_request(lambda r: "/impact/" in r.url and "max_depth=" in r.url):
        page.mouse.click(box["x"] + box["width"] * 0.2, box["y"] + box["height"] / 2)
    assert int(slider.input_value()) < 5
    assert live.inner_text() == "Hop depth: %s" % slider.input_value()


# --- focus is never obscured ----------------------------------------


def _uncovered(page, locator):
    """The focused control is what sits at its own centre, so nothing covers it.
    Returns True, or a description of what is on top instead."""
    return page.evaluate(
        """(el) => {
            const r = el.getBoundingClientRect();
            const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            if (!!top && (top === el || el.contains(top) || top.contains(el))) return true;
            return "covered by " + (top ? top.tagName + "." + String(top.className && top.className.baseVal !== undefined ? top.className.baseVal : top.className).slice(0, 80) : "nothing")
              + " at " + [r.left, r.top, r.width, r.height].map(Math.round).join(",") + " for " + el.tagName + ":" + (el.innerText || el.id || "").slice(0, 30);
        }""",
        locator.element_handle(),
    )


def test_focus_is_never_obscured_by_the_drawer_or_the_side_panel(
    page, live_server, seeded, graph
):
    """With the drawer open, focus is inside it and uncovered. On the map, the last
    element's focus ring is fully visible; closing the side panel from a map element
    moves neither the focus nor the selection."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_selector("[data-ask-row]")
    _rows(page).first.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    page.wait_for_function("() => !!document.activeElement.closest('#drawer-provenance [role=dialog]')")
    _settled(page, "#drawer-provenance [role=dialog]")
    for _ in range(6):
        state = _focus(page)
        assert state["inDialog"], "focus left the open drawer: %r" % state
        assert _uncovered(page, page.locator(":focus")) is True
        page.keyboard.press("Tab")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")

    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    expect(page.locator("#twin-rail")).to_be_visible()
    canvas = _measure(page.locator("[data-twin-canvas]"))
    assert canvas["width"] >= 600, "the map is narrower than 600 CSS pixels: %r" % canvas

    page.locator("#twin-hop-depth").focus()
    last = page.locator("[data-graph-nodes] button").last
    last_id = last.get_attribute("data-node")
    reached = _tab_until(page, lambda s: s["node"] == last_id, "the last map element")
    assert reached["ring"] and reached["inViewport"]
    assert _uncovered(page, last) is True

    # Choose it, then close the side panel with Escape from that element.
    page.keyboard.press("Enter")
    expect(last).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Escape")
    expect(page.locator("#twin-rail")).to_be_hidden()
    assert _focus(page)["node"] == last_id
    expect(last).to_have_attribute("aria-pressed", "true")
    assert page.locator("button[data-node][aria-pressed=true]").count() == 1

    # And back again with the panel's own control: the selection is where it was.
    page.locator("#twin-rail-toggle").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#twin-rail")).to_be_visible()
    expect(last).to_have_attribute("aria-pressed", "true")


# --- the drawer is named, escapable, and gives focus back -------------


def test_the_drawer_is_named_by_the_element_and_kind_and_returns_focus(
    page, live_server, seeded, graph
):
    """The dialog's accessible name carries the element name and the kind badge; Escape
    closes it; focus returns to the very control that opened it."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_selector("[data-ask-row]")

    gateway = _row_for(page, graph["names"]["gateway"], "explicit")
    opener = gateway.get_by_role("button", name="Why?")
    opener.click()
    dialog = page.get_by_role("dialog", name=re.compile(re.escape(graph["names"]["gateway"]) + r".*Measured"))
    dialog.wait_for(state="visible")
    title = page.locator("#drawer-title-provenance")
    assert graph["names"]["gateway"] in title.inner_text()
    assert "Measured" in title.inner_text()
    assert page.locator("#drawer-provenance [role=dialog]").get_attribute("aria-labelledby") == "drawer-title-provenance"
    assert page.locator("#drawer-provenance [role=dialog]").get_attribute("aria-modal") == "true"
    # An explicit connection carries no sentence, and none is invented.
    expect(page.locator("#drawer-provenance [data-plain-terms]")).to_be_hidden()

    page.keyboard.press("Escape")
    page.locator("#drawer-provenance [role=dialog]").wait_for(state="hidden")
    page.wait_for_function("(el) => document.activeElement === el", arg=opener.element_handle())

    # The same drawer for a worked-out connection, named for what is true of it: the
    # element and "Worked out", never "Measured".
    derived = _row_for(page, graph["names"]["ops"], "derived")
    derived.get_by_role("button", name="Why?").click()
    named = page.get_by_role(
        "dialog", name=re.compile("^" + re.escape(graph["names"]["ops"]) + r"\s+Worked out$"))
    named.wait_for(state="visible")
    assert "Measured" not in page.locator("#drawer-title-provenance").inner_text()
    expect(page.locator("#drawer-provenance [data-plain-terms]")).to_be_visible()


# --- the map has a real text equivalent -----------------------------


def _drawn_edges(page):
    return sorted(page.eval_on_selector_all(
        "svg g.intel-edge", "els => els.map(e => e.getAttribute('data-edge'))"))


def _listed_edges(page):
    return sorted(page.eval_on_selector_all(
        "[data-map-row]", "els => els.map(e => e.getAttribute('data-edge-row'))"))


def test_the_map_table_lists_every_edge_the_graph_draws_and_no_others(
    page, live_server, seeded, graph
):
    """The table is on the page with no toggle, is built from the same answer as the
    picture, and moves with the hop depth and the worked-out toggle."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    names = graph["names"]

    # No control anywhere on the page swaps the picture for the table.
    assert page.get_by_role("button", name=re.compile(r"table|list|text", re.I)).count() == 0
    table = page.locator("table[data-map-table]")
    expect(table).to_be_visible()
    payload = _impact_api(page, live_server, graph["service"])
    assert len(payload["rows"]) == 4
    assert page.locator("[data-map-row]").count() == 4
    assert _drawn_edges(page) == _listed_edges(page)
    assert len(_drawn_edges(page)) == 4

    caption = table.locator("caption").inner_text()
    assert caption == "Connections for %s: 4 things, 4 connections" % names["service"]
    graph_label = page.locator("[data-graph-svg]")
    assert graph_label.get_attribute("role") == "img"
    assert names["service"] in graph_label.get_attribute("aria-label")
    assert "4 things, 4 connections" in graph_label.get_attribute("aria-label")

    kinds = page.eval_on_selector_all(
        "[data-map-row]", "els => els.map(e => [e.getAttribute('data-kind'), e.children[3].innerText.trim()])")
    assert sorted(kinds) == [["derived", "Worked out"], ["explicit", "Explicit"],
                             ["explicit", "Explicit"], ["explicit", "Explicit"]]
    every_target = {names["gateway"], names["portal"], names["ops"]}
    targets = page.eval_on_selector_all(
        "[data-map-row]", "els => els.map(e => e.children[2].innerText.trim())")
    assert set(targets) == every_target
    sources = page.eval_on_selector_all(
        "[data-map-row]", "els => els.map(e => e.children[0].innerText.trim())")
    assert set(sources) <= {names["service"], names["gateway"]}
    # The centre is named in the caption; every other element is in a Source or Target cell.
    assert (set(sources) | set(targets)) | {names["service"]} == set(names.values())

    # Hop depth changes the picture and the table together.
    page.locator("#twin-hop-depth").focus()
    with page.expect_request(lambda r: "max_depth=1" in r.url and "/impact/" in r.url):
        page.keyboard.press("Home")
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 2")
    assert _drawn_edges(page) == _listed_edges(page) and len(_drawn_edges(page)) == 2
    assert page.locator("table[data-map-table] caption").inner_text() == (
        "Connections for %s: 3 things, 2 connections" % names["service"])
    with page.expect_request(lambda r: "max_depth=3" in r.url and "/impact/" in r.url):
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 4")

    # The worked-out toggle does the same.
    with page.expect_request(lambda r: "include_derived=false" in r.url and "/impact/" in r.url):
        page.locator("#twin-show-derived").uncheck()
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 3")
    assert _drawn_edges(page) == _listed_edges(page) and len(_drawn_edges(page)) == 3
    assert page.locator("[data-map-row][data-kind=derived]").count() == 0
    page.locator("#twin-show-derived").check()
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 4")

    # Each row's Why? opens the same drawer.
    derived_row = page.locator("[data-map-row][data-kind=derived]")
    derived_row.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    assert page.locator("#drawer-provenance [data-plain-terms]").inner_text() == next(
        row["relation"]["plain_terms"] for row in payload["rows"] if row["relation"]["kind"] == "derived")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")

    # A row can re-centre the map on its target.
    page.locator("[data-map-row][data-kind=explicit]").filter(
        has=page.get_by_role("cell", name=names["gateway"], exact=True).nth(0)
    ).get_by_role("button", name="Centre on this").first.click()
    page.wait_for_function(
        "(name) => document.querySelector('table[data-map-table] caption')"
        ".textContent.startsWith('Connections for ' + name)",
        arg=names["gateway"])


# --- technical detail lives only inside Full detail -------------------

LEVEL_THREE = """
(values) => {
  const scope = document.querySelector('main');
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  const found = [];
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent;
    if (!text.trim()) continue;
    const el = node.parentElement;
    if (el.closest('script, style')) continue;
    if (el.closest('[data-full-detail-region]')) continue;
    for (const value of values) {
      if (text.includes(value)) found.push([value, text.trim().slice(0, 80)]);
    }
  }
  return found;
}
"""


def test_technical_detail_appears_only_inside_full_detail(page, live_server, seeded, graph):
    """The ArchiMate type, rule, raw confidence, engine version and the time a
    connection was worked out are on the page, and only inside a Full detail region."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_selector("[data-ask-row]")
    technical = ["BusinessActor", "ApplicationComponent", "serving-through-serving", "0.82"]
    assert page.evaluate(LEVEL_THREE, technical) == []
    _row_for(page, graph["names"]["ops"], "derived").locator("[data-full-detail-toggle]").click()
    region = _row_for(page, graph["names"]["ops"], "derived").locator("[data-full-detail-region]")
    expect(region).to_be_visible()
    inside = region.inner_text()
    for value in ("BusinessActor", "serving-through-serving", "0.82", "1.0", "business"):
        assert value in inside
    assert re.search(r"\d{4}-\d{2}-\d{2}T", inside), "the time it was worked out is missing"
    assert page.evaluate(LEVEL_THREE, technical + ["1.0"]) == []

    # Same on the map, with the drawer open as well.
    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    assert page.evaluate(LEVEL_THREE, technical) == []
    page.locator("[data-map-row][data-kind=derived]").get_by_role("button", name="Why?").click()
    page.locator("#drawer-provenance [role=dialog]").wait_for(state="visible")
    assert page.evaluate(LEVEL_THREE, technical) == []


# --- the empty workspace ---------------------------------------------


@pytest.mark.parametrize("path", ["/intelligence/ask", "/intelligence/twin-map"])
def test_an_empty_workspace_says_so_and_leads_to_setup(path, page, live_server, empty_tenant):
    """Nothing modelled: the page shows the setup state in place of the picker, its
    button lands on the guided setup, and nothing mentions connectors or onboarding."""
    _login(page, live_server, empty_tenant["email"])
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    main = page.locator("main")
    expect(main.get_by_role("heading", name="Nothing is modelled yet")).to_be_visible()
    assert "Add your systems and teams, and this is where you will ask questions about them." in main.inner_text()
    assert main.get_by_role("combobox").count() == 0
    text = main.inner_text()
    assert not re.search(r"connector|onboarding", text, re.I)
    assert page.locator("main a[href*='onboarding']").count() == 0
    button = main.get_by_role("link", name="Set up your workspace")
    assert button.get_attribute("href") == "/dashboard/overview"
    button.click()
    page.wait_for_url(re.compile(r"/dashboard/overview"))
    page.wait_for_selector("text=Set up your workspace")


@pytest.mark.parametrize("path", ["/intelligence/ask", "/intelligence/twin-map"])
def test_a_populated_workspace_shows_the_picker_and_no_setup_state(
    path, page, live_server, seeded, graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    main = page.locator("main")
    assert "Nothing is modelled yet" not in main.inner_text()
    assert main.get_by_text("Set up your workspace").count() == 0
    if path.endswith("/ask"):
        page.wait_for_selector("#ask-question-impact")
    else:
        page.wait_for_selector("#twin-picker-input")


# --- absence, the not-worked-out state, busy and error states -


def _ask_in_bare_tenant(page, live_server, bare):
    _login(page, live_server, bare["email"])
    _dismiss_first_run(page)
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", bare["noun"])
    _pick_by_click(page, "ask", bare["head_name"])


def test_missing_owner_missing_health_and_no_derivation_read_as_three_distinct_reasons(
    page, live_server, bare_tenant
):
    """A tenant with no owner, no health signal and nothing worked out shows three
    different statements, each as text a screen reader reads, and not a zero."""
    _ask_in_bare_tenant(page, live_server, bare_tenant)
    page.wait_for_selector("[data-ask-row]")
    row = _rows(page).first
    owner = row.get_by_label("Owner: Not recorded")
    assert owner.count() == 1 and "Not recorded" in owner.inner_text()
    health = row.get_by_label("Health: Not recorded yet")
    assert health.count() == 1 and "Not recorded yet" in health.inner_text()
    banner = page.get_by_text("We haven't worked out the indirect connections for your model yet.")
    expect(banner).to_be_visible()
    expect(page.get_by_role("button", name="Work them out now")).to_be_visible()
    # No zero stands in for an absent value anywhere in the results.
    cells = page.eval_on_selector_all(
        "#ask-results *", "els => els.filter(e => e.children.length === 0)"
        ".map(e => e.textContent.trim()).filter(t => t === '0' || t === '0%')")
    assert cells == []
    # No badge on these pages is an icon on its own: each carries its words.
    badges = page.locator("#ask-results span.rounded-md:has(> svg), #ask-results span.rounded-md:has(> i[data-lucide])")
    for index in range(badges.count()):
        badge = badges.nth(index)
        if badge.is_visible():
            assert badge.inner_text().strip(), "an icon-only badge is on the page"


def test_not_computed_is_a_state_with_a_working_action(page, live_server, bare_tenant):
    """The sentence and a working button, never a zero or a blank. The button posts the
    tenant scope, then the page asks again; a 409 reads as the stated line."""
    _ask_in_bare_tenant(page, live_server, bare_tenant)
    page.wait_for_selector("[data-ask-row]")
    sentence = "We haven't worked out the indirect connections for your model yet."
    banner = page.locator("[role=status]", has_text=sentence)
    expect(banner).to_be_visible()
    assert banner.inner_text().strip() != "0"

    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/api/v1/intelligence/derivation/recompute")
    ) as posted:
        with page.expect_request(lambda r: "/api/v1/intelligence/impact/" in r.url and r.method == "GET") as requery:
            page.get_by_role("button", name="Work them out now").click()
    assert json.loads(posted.value.post_data) == {"scope": "tenant"}
    assert requery.value.url.split("?")[0].endswith("/impact/%s" % bare_tenant["head"])


def test_a_recalculation_already_running_reads_as_a_plain_line(page, live_server, bare_tenant):
    """A 409 from the recompute shows the stated sentence and invents nothing."""
    page.route(
        "**/api/v1/intelligence/derivation/recompute",
        lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"success": False, "error": "locked", "code": "RECOMPUTE_LOCKED"})),
    )
    _ask_in_bare_tenant(page, live_server, bare_tenant)
    page.wait_for_selector("[data-ask-row]")
    page.get_by_role("button", name="Work them out now").click()
    # Both notices carry the button and its line; only the one that is showing counts.
    line = page.locator("[data-not-computed-notice]").get_by_text(
        "A recalculation is already running. Try again shortly.")
    line.wait_for(state="visible")
    expect(page.get_by_text("We haven't worked out the indirect connections for your model yet.")).to_be_visible()
    assert page.locator("[data-ask-row]").count() == 1


def test_the_results_are_busy_while_asking_and_errors_are_announced(
    page, live_server, seeded, graph
):
    """aria-busy is true while an answer is on its way and false after; a failed answer
    reads as a plain sentence beside the could-not-measure badge inside a status region;
    an answer with no rows says nothing is recorded; nothing invented fills any of them."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])

    held = []
    page.route("**/api/v1/intelligence/impact/**", lambda route: held.append(route))
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_function("() => document.querySelector('#ask-results').getAttribute('aria-busy') === 'true'")
    expect(page.get_by_text("Tracing the impact chain…").first).to_be_visible()
    assert page.locator("#ask-results [role=status]").count() >= 1
    held[0].continue_()
    page.wait_for_selector("[data-ask-row]")
    assert page.locator("#ask-results").get_attribute("aria-busy") == "false"
    page.unroute("**/api/v1/intelligence/impact/**")

    # A failed answer.
    page.route(
        "**/api/v1/intelligence/impact/**",
        lambda route: route.fulfill(status=500, content_type="application/json",
                                    body=json.dumps({"success": False, "error": "down"})),
    )
    page.locator("#ask-picker-input").fill("")
    page.locator("#ask-picker-input").press_sequentially(graph["noun"], delay=15)
    page.wait_for_selector("#ask-picker-listbox [role=option]")
    page.locator("#ask-picker-listbox [role=option]", has_text=graph["names"]["service"]).click()
    failure = page.locator("#ask-results [role=status]", has_text="We could not answer that just now.")
    failure.wait_for(state="visible")
    assert "Could not measure" in failure.inner_text()
    assert page.locator("[data-ask-row]").count() == 0
    page.unroute("**/api/v1/intelligence/impact/**")

    # An element nothing depends on.
    page.locator("#ask-picker-input").fill("")
    page.locator("#ask-picker-input").press_sequentially(graph["noun"], delay=15)
    page.wait_for_selector("#ask-picker-listbox [role=option]")
    page.locator("#ask-picker-listbox [role=option]", has_text=graph["names"]["portal"]).click()
    empty = page.locator("#ask-results [role=status]", has_text="Nothing is recorded as depending on this yet")
    empty.wait_for(state="visible")
    assert "We found no connections from this one." in empty.inner_text()


def test_a_row_the_answer_cannot_name_shows_the_absence_and_no_invented_sentence(
    page, live_server, seeded, graph
):
    """An element missing from the answer's name map shows "Not recorded", never a bare
    id or a blank; a derived row with no sentence shows none; a stale row says when it was
    last worked out with a clock and words."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    body = {
        "success": True,
        "data": {
            "rows": [{
                "element_id": 987654,
                "relation": {
                    "kind": "derived", "type": "Serving", "depth": 2, "rule_id": "test-rule",
                    "chain": [], "chain_elements": [1, 987654], "confidence": None,
                    "provenance": "derivation", "computed_at": "2026-01-02T10:30:00",
                    "stale": True, "derived_id": 77, "engine_version": "1.0", "plain_terms": None,
                },
                "owner": None, "reason": "no_ownership_recorded",
            }],
            "summary": {"explicit_count": 0, "derived_count": 1, "stale_count": 1,
                        "derivation_state": "stale", "latency_ms": 1},
            "reasons": [], "elements": {},
        },
        "meta": {},
    }
    page.route("**/api/v1/intelligence/impact/**",
               lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(body)))
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    row = page.locator("[data-ask-row]")
    row.wait_for(state="visible")
    heading = row.get_by_role("heading")
    assert "987654" not in row.inner_text()
    assert heading.inner_text().strip() == "Not recorded"
    assert row.get_by_label("Last worked out").count() == 1
    assert "Last worked out 2 Jan 2026" in row.inner_text()
    assert "may be out of date." in row.inner_text()

    row.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    assert dialog.locator("[data-plain-terms]").count() == 1
    expect(dialog.locator("[data-plain-terms]")).to_be_hidden()
    assert "We worked this out because" not in dialog.inner_text()
    expect(dialog.get_by_text("Not recorded").first).to_be_visible()
    assert "Last worked out" in dialog.inner_text()


# --- a model that has gone out of date ------------------------------------------

STALE_LINE = re.compile(r"Last worked out .+ — may be out of date\.")


@pytest.fixture
def stale_tenant(live_server):
    """A fresh organisation whose one worked-out connection has gone out of date.

    It is made stale the way the product makes it stale: a person edits a relationship
    the connection was worked out from, and the invalidation listener marks it. One per
    test, in an organisation of its own, because asking for the connections to be worked
    out again rewrites every worked-out connection in the organisation it runs in."""
    import datetime

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Stale Org %s" % suffix, slug="stale-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="stale.%s@example.com" % suffix, first_name="Stale", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            onboarding_completed_at=datetime.datetime.utcnow(),
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        org_id, email = org.id, user.email
    stale_graph = seed_impact_graph(org_id, "Stalepay")
    stored = mark_derived_stale(stale_graph)
    stale_graph.update(email=email, stored=stored)
    return stale_graph


def _stale_answer(page, base, graph, include_stale):
    return _impact_api(page, base, graph["service"], include_stale="true" if include_stale else "false")


def _ask_about_stale(page, base, stale):
    _login(page, base, stale["email"])
    _dismiss_first_run(page)
    _open_ask(page, base)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", stale["noun"])
    with page.expect_request(lambda r: "/api/v1/intelligence/impact/" in r.url) as asked:
        _pick_by_click(page, "ask", stale["names"]["service"])
    page.wait_for_selector("[data-ask-row]")
    return asked.value


def test_the_tenant_really_is_stale_the_way_the_product_makes_it_stale(
    page, live_server, stale_tenant
):
    """Ground truth for the tests below, read from the stored row and from the endpoint:
    the edit alone set stale, its time and its reason; an answer that does not ask for
    out-of-date connections leaves the worked-out one out and says the state is stale;
    one that asks for them holds it, marked."""
    stored = stale_tenant["stored"]
    assert stored["stale"] is True
    assert stored["stale_since"] is not None
    assert stored["stale_reason"] == "relationship_updated"

    _login(page, live_server, stale_tenant["email"])
    without = _stale_answer(page, live_server, stale_tenant, include_stale=False)
    assert without["summary"]["derivation_state"] == "stale"
    assert without["summary"]["derived_count"] == 0
    assert without["summary"]["explicit_count"] == 3
    assert [r for r in without["rows"] if r["relation"]["kind"] == "derived"] == []

    held = _stale_answer(page, live_server, stale_tenant, include_stale=True)
    derived = [r for r in held["rows"] if r["relation"]["kind"] == "derived"]
    assert held["summary"]["derived_count"] == 1 and held["summary"]["stale_count"] == 1
    assert len(derived) == 1 and derived[0]["relation"]["stale"] is True


def test_ask_still_shows_a_stale_connection_marked_with_a_notice_and_an_action(
    page, live_server, stale_tenant
):
    """The page asks for out-of-date connections, so the worked-out one is on the page:
    on its row with a clock and "Last worked out ... may be out of date.", in the
    drawer the same way, under a notice with the recalculation button. Working them
    out again brings back a current connection and takes the notice away."""
    names = stale_tenant["names"]
    request = _ask_about_stale(page, live_server, stale_tenant)
    assert "include_derived=true" in request.url and "include_stale=true" in request.url

    assert _rows(page).count() == 4
    derived = _row_for(page, names["ops"], "derived")
    expect(derived).to_have_count(1)
    expect(derived.get_by_label("Last worked out")).to_have_count(1)
    assert STALE_LINE.search(derived.inner_text())

    notice = page.locator("[data-stale-notice]")
    expect(notice).to_be_visible()
    assert notice.get_attribute("role") == "status"
    expect(notice).to_contain_text("The connections we worked out may be out of date.")
    expect(notice.get_by_label("Last worked out")).to_have_count(1)
    button = notice.get_by_role("button", name="Work them out now")
    expect(button).to_be_visible()
    expect(page.locator("[data-not-computed-notice]")).to_be_hidden()

    # Nothing is hidden from the count: it names every connection on the page.
    expect(page.locator("#ask-question-panel-impact [role=status]")).to_have_text(
        "4 connections for %s" % names["service"])

    # The drawer says the same, and is named for what is true of the connection.
    derived.get_by_role("button", name="Why?").click()
    dialog = page.get_by_role(
        "dialog", name=re.compile("^" + re.escape(names["ops"]) + r"\s+Worked out$"))
    dialog.wait_for(state="visible")
    expect(dialog.locator("[data-plain-terms]")).to_be_visible()
    served = next(
        r["relation"]["plain_terms"]
        for r in _stale_answer(page, live_server, stale_tenant, include_stale=True)["rows"]
        if r["relation"]["kind"] == "derived")
    assert dialog.locator("[data-plain-terms]").inner_text() == served
    assert STALE_LINE.search(dialog.inner_text())
    expect(dialog.get_by_label("Last worked out")).to_have_count(1)
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")

    # The action: it posts the tenant scope, then asks again.
    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/api/v1/intelligence/derivation/recompute")
    ) as posted:
        with page.expect_request(lambda r: "/api/v1/intelligence/impact/" in r.url) as again:
            button.click()
    assert json.loads(posted.value.post_data) == {"scope": "tenant"}
    assert "include_stale=true" in again.value.url
    expect(page.locator("[data-stale-notice]")).to_be_hidden()
    current = _row_for(page, names["ops"], "derived")
    expect(current).to_have_count(1)
    assert "may be out of date" not in current.inner_text()
    # Focus did not fall back to the top of the page.
    page.wait_for_function("() => document.activeElement.id === 'ask-results-heading'")


def test_twin_map_still_draws_a_stale_connection_marked_and_the_table_agrees(
    page, live_server, stale_tenant
):
    """On the map the stale connection is drawn dashed with a badge that says it may be
    out of date, listed in the table as "Worked out, may be out of date", and the two
    agree edge for edge; the notice and the action are there."""
    names = stale_tenant["names"]
    _login(page, live_server, stale_tenant["email"])
    _dismiss_first_run(page)
    _open_twin_map(page, live_server, stale_tenant["service"])
    _wait_for_map(page)

    assert _drawn_edges(page) == _listed_edges(page)
    assert len(_drawn_edges(page)) == 4
    assert page.locator("svg path[data-kind=derived]").count() == 1
    assert page.locator("svg path[data-kind=derived]").get_attribute("stroke-dasharray") in ("5,5", "5, 5")
    badge = page.locator("svg g.intel-badge")
    assert badge.count() == 1
    assert badge.locator("text").text_content() == "Worked out, may be out of date"
    assert badge.locator("[aria-label='Last worked out']").count() == 1

    kinds = page.eval_on_selector_all(
        "[data-map-row]", "els => els.map(e => [e.getAttribute('data-kind'), e.children[3].innerText.trim()])")
    assert sorted(kinds) == [["derived", "Worked out, may be out of date"], ["explicit", "Explicit"],
                             ["explicit", "Explicit"], ["explicit", "Explicit"]]
    expect(page.locator("table[data-map-table] caption")).to_have_text(
        "Connections for %s: 4 things, 4 connections" % names["service"])
    expect(page.locator("#twin-picker-input").locator("xpath=ancestor::section//*[@role='status']")).to_have_text(
        "4 connections for %s" % names["service"])
    derived_row = page.locator("[data-map-row][data-kind=derived]")
    expect(derived_row.get_by_label("Last worked out")).to_have_count(1)

    notice = page.locator("[data-stale-notice]")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("The connections we worked out may be out of date.")
    expect(notice.get_by_role("button", name="Work them out now")).to_be_visible()

    derived_row.get_by_role("button", name="Why?").click()
    dialog = page.get_by_role(
        "dialog", name=re.compile("^" + re.escape(names["ops"]) + r"\s+Worked out$"))
    dialog.wait_for(state="visible")
    assert STALE_LINE.search(dialog.inner_text())
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")

    # With worked-out connections switched off the page asks without them, the table and
    # the map still agree, and the notice about the model stays.
    page.locator("#twin-show-derived").focus()
    with page.expect_request(lambda r: "include_derived=false" in r.url and "/impact/" in r.url):
        page.keyboard.press("Space")
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 3")
    assert _drawn_edges(page) == _listed_edges(page) and len(_drawn_edges(page)) == 3
    expect(page.locator("[data-stale-notice]")).to_be_visible()

    # Recalculating from the map brings the current connection back and clears the notice.
    page.locator("#twin-show-derived").check()
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 4")
    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/api/v1/intelligence/derivation/recompute")
    ):
        page.locator("[data-stale-notice]").get_by_role("button", name="Work them out now").click()
    expect(page.locator("[data-stale-notice]")).to_be_hidden()
    page.wait_for_function("() => document.querySelectorAll('[data-map-row]').length === 4")
    assert _drawn_edges(page) == _listed_edges(page)
    expect(page.locator("[data-map-row][data-kind=derived] [aria-label='Last worked out']")).to_be_hidden()
    expect(page.locator("svg g.intel-badge text")).to_have_text("Worked out")
    page.wait_for_function("() => document.activeElement.id === 'twin-map-heading'")


def _without_worked_out(route):
    """Answer as the endpoint does when out-of-date connections are not asked for: the
    real answer, with its worked-out rows taken out and its state left as stale."""
    response = route.fetch()
    body = response.json()
    data = body["data"]
    data["rows"] = [r for r in data["rows"] if r["relation"]["kind"] != "derived"]
    data["summary"]["derived_count"] = 0
    data["summary"]["stale_count"] = 0
    data["summary"]["derivation_state"] = "stale"
    route.fulfill(response=response, body=json.dumps(body))


def test_an_answer_that_leaves_worked_out_connections_out_says_so_in_its_counts(
    page, live_server, stale_tenant
):
    """Supplement, for an answer the page's own request no longer produces: if an answer
    is stale and lists no worked-out connection, the notice says they are not shown and
    neither the count on Ask nor the caption on the map pretends the list is complete."""
    names = stale_tenant["names"]
    _login(page, live_server, stale_tenant["email"])
    _dismiss_first_run(page)
    page.route("**/api/v1/intelligence/impact/**", _without_worked_out)

    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", stale_tenant["noun"])
    _pick_by_click(page, "ask", names["service"])
    page.wait_for_selector("[data-ask-row]")
    assert _rows(page).count() == 3
    notice = page.locator("[data-stale-notice]")
    expect(notice).to_contain_text("Some connections we worked out are not shown because they may be out of date.")
    expect(notice.get_by_role("button", name="Work them out now")).to_be_visible()
    expect(page.locator("#ask-question-panel-impact [role=status]")).to_have_text(
        "3 connections for %s, worked-out connections not shown" % names["service"])

    _open_twin_map(page, live_server, stale_tenant["service"])
    _wait_for_map(page)
    expect(page.locator("table[data-map-table] caption")).to_have_text(
        "Connections for %s: 4 things, 3 connections, worked-out connections not shown" % names["service"])
    expect(page.locator("[data-stale-notice]")).to_contain_text("are not shown because they may be out of date")


# --- what the picker's status region says --------------------------------------


def test_the_status_region_counts_results_after_typing_and_connections_after_choosing(
    page, live_server, bare_tenant
):
    """Two different messages, each with its own singular: "1 result for ..." while
    typing, then "1 connection for ..." once an element is chosen. Never "1 results"."""
    _login(page, live_server, bare_tenant["email"])
    _dismiss_first_run(page)
    _open_ask(page, live_server)
    page.locator("#ask-question-impact").click()
    box = page.locator("#ask-picker-input")
    status = page.locator("#ask-question-panel-impact [role=status]")

    box.press_sequentially(bare_tenant["head_name"], delay=15)
    page.wait_for_selector("#ask-picker-listbox [role=option]")
    expect(status).to_have_text("1 result for %s" % bare_tenant["head_name"])
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-ask-row]")
    expect(status).to_have_text("1 connection for %s" % bare_tenant["head_name"])

    box.fill("")
    box.press_sequentially(bare_tenant["noun"], delay=15)
    page.wait_for_selector("#ask-picker-listbox [role=option]")
    expect(status).to_have_text("2 results for %s" % bare_tenant["noun"])


def test_the_map_panel_control_is_named_for_the_panel_not_as_a_second_disclosure(
    page, live_server, seeded, graph
):
    """The control that collapses the side panel carries the panel's own name, so it is
    not read as a second "show more" next to "Full detail"."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_twin_map(page, live_server, graph["service"])
    _wait_for_map(page)
    toggle = page.get_by_role("button", name="Selected element")
    expect(toggle).to_have_count(1)
    assert toggle.get_attribute("id") == "twin-rail-toggle"
    assert page.get_by_role("button", name="Details").count() == 0
    expect(page.locator("#twin-rail")).to_have_attribute("aria-label", "Selected element")
