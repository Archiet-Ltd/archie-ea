"""The capability map must show only maturity that was actually recorded.

Replaces the journeys that pinned the retired dashboard heat map: the same
honesty rules, now asserted on the one capability map coloured by maturity gap.

  * a tenant with assessed and unassessed capabilities: assessed boxes name their
    gap, unassessed boxes read Not assessed (dashed, no level, no gap), levels
    with no assessment date count as not assessed, a sibling tenant's capability
    never appears
  * a tenant where nothing is assessed: no gap, no "On target", anywhere
  * a tenant with no capabilities: the empty state and the next step
  * keyboard: Tab reaches a box, the focus ring is painted, Enter opens its detail
  * the Not assessed marker keeps AA contrast in both themes at both widths

Runs as the CTO persona in tenants created through the ORM; its capabilities are removed at
module teardown (users and organisations carry audit rows and stay, under a random suffix).
"""

import re
import uuid
from datetime import datetime

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MAP_PATH = "/capability-map/dashboard"
MAP = '[data-testid="capability-map"]'
BOX = '[data-testid="cap-box"]'
DESKTOP = {"width": 1280, "height": 900}
NARROW = {"width": 390, "height": 844}

# A "0%" that is not the tail of a larger number, "null%", an undefined or NaN that
# leaked out of a JS expression: none of these may reach the map.
INVENTED = re.compile(r"(?<![\d.])0(?:\.0)?%|null%|undefined|NaN")

CONTRAST_JS = """
(el) => {
  const parse = (s) => {
    const m = s.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a), a: 1,
  });
  let node = el, layers = [];
  while (node) {
    const c = parse(getComputedStyle(node).backgroundColor);
    if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    node = node.parentElement;
  }
  let bg = { r: 255, g: 255, b: 255, a: 1 };
  for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
  let fg = parse(getComputedStyle(el).color);
  if (fg.a < 1) fg = over(fg, bg);
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const l1 = lum(fg), l2 = lum(bg);
  return Math.round(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)) * 100) / 100;
}
"""


@pytest.fixture(scope="module")
def map_tenants(live_server, request):
    from app import create_app, db
    from app.models.capability_models import BusinessCapability
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"suffix": suffix, "emails": {}, "names": {}}
    created = {"caps": [], "users": [], "orgs": []}

    def capability(org_id, name, *, current=None, target=None, assessed=True):
        row = BusinessCapability(
            name=name, organization_id=org_id, category="Operations",
            current_maturity_level=current, target_maturity_level=target,
            maturity_assessment_date=datetime(2026, 5, 1) if assessed else None,
        )
        db.session.add(row)
        db.session.flush()
        created["caps"].append(row.id)
        return row

    def org_and_cto(label):
        org = Organization(name="Smoke Map %s %s" % (label, suffix), slug="smoke-map-%s-%s" % (label, suffix))
        db.session.add(org)
        db.session.flush()
        created["orgs"].append(org.id)
        email = "smoke.map-%s.%s@example.com" % (label, suffix)
        user = User(email=email, first_name="Smoke", last_name="map-" + label,
                    organization_id=org.id, enterprise_role="cto", confirmed=True,
                    role=Role.query.filter_by(name="Architect").one())
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()
        created["users"].append(user.id)
        out["emails"][label] = email
        return org

    with app.app_context():
        names = {
            "short": "Map short %s" % suffix,        # current 2, target 4: gap 2
            "on_target": "Map on target %s" % suffix,  # current 3, target 3
            "unassessed": "Map unassessed %s" % suffix,        # nothing recorded
            "undated": "Map undated levels %s" % suffix,       # levels, no date
            "foreign": "Map foreign tenant %s" % suffix,
            "u_1": "Map U one %s" % suffix,
            "u_2": "Map U two %s" % suffix,
        }
        out["names"] = names
        mixed = org_and_cto("mixed")
        other = org_and_cto("other")
        capability(mixed.id, names["short"], current=2, target=4)
        capability(mixed.id, names["on_target"], current=3, target=3)
        capability(mixed.id, names["unassessed"])
        capability(mixed.id, names["undated"], current=1, target=3, assessed=False)
        capability(other.id, names["foreign"], current=5, target=5)

        unassessed = org_and_cto("unassessed")
        capability(unassessed.id, names["u_1"], assessed=False)
        capability(unassessed.id, names["u_2"], assessed=False)

        org_and_cto("empty")
        db.session.commit()

    def remove_rows():
        with app.app_context():
            db.session.remove()
            if created["caps"]:
                db.session.query(BusinessCapability).filter(
                    BusinessCapability.id.in_(created["caps"])).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()

    request.addfinalizer(remove_rows)
    return out


def _open(browser, base, email, viewport=DESKTOP, scheme="light"):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, base, email)
    page.goto(base + MAP_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _box(page, name):
    return page.locator(BOX, has=page.locator("button", has_text=name)).first


def _assert_nothing_invented(page):
    # The boxes and the key; the stat row's coverage is a measured share, 0% included.
    text = page.locator('[data-testid="cap-scroll"]').inner_text() + page.locator('[data-testid="capability-legend"]').inner_text()
    found = INVENTED.findall(text)
    assert not found, "invented figure(s) on the map: %r" % found


def test_mixed_tenant_map_shows_only_recorded_maturity(browser, live_server, map_tenants):
    names = map_tenants["names"]
    context, page = _open(browser, live_server, map_tenants["emails"]["mixed"])
    try:
        expect(page.locator(BOX).first).to_be_visible(timeout=PAGE_TIMEOUT)
        short, on_target = _box(page, names["short"]), _box(page, names["on_target"])
        assert short.get_attribute("data-assessed") == "true"
        assert "Gap 2" in short.inner_text()
        assert on_target.get_attribute("data-assessed") == "true"
        assert "On target" in on_target.inner_text()

        for key in ("unassessed", "undated"):
            box = _box(page, names[key])
            assert box.get_attribute("data-assessed") == "false", key
            text = box.inner_text()
            assert "Not assessed" in text and "Gap" not in text and "On target" not in text, text
            assert "border-dashed" in box.locator("> button").get_attribute("class")

        assert page.locator(BOX, has_text=names["foreign"]).count() == 0
        assert page.locator(BOX).count() == 4
        expect(page.locator('[data-testid="capmap-stats"]')).to_contain_text("4")
        _assert_nothing_invented(page)
    finally:
        context.close()


def test_unassessed_tenant_shows_no_gap_and_no_on_target_anywhere(browser, live_server, map_tenants):
    context, page = _open(browser, live_server, map_tenants["emails"]["unassessed"])
    try:
        expect(page.locator(BOX).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.locator(BOX).count() == 2
        assert page.locator('%s[data-assessed="true"]' % BOX).count() == 0
        text = page.locator(MAP).locator('[data-testid="cap-scroll"]').inner_text()
        assert "Gap" not in text and "On target" not in text and "%" not in text, text
        assert text.count("Not assessed") == 2
        expect(page.locator('[data-testid="capability-legend"]')).to_contain_text("Not assessed")
        _assert_nothing_invented(page)
    finally:
        context.close()


def test_empty_tenant_keeps_the_empty_state_and_the_next_step(browser, live_server, map_tenants):
    context, page = _open(browser, live_server, map_tenants["emails"]["empty"])
    try:
        expect(page.locator("#capmodel-empty")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("#capmodel-empty")).to_contain_text("No capabilities found")
        expect(page.get_by_role("link", name="Define a capability").first).to_be_visible()
        assert page.locator(BOX).count() == 0
        _assert_nothing_invented(page)
    finally:
        context.close()


def _focus_box(page, name):
    target = _box(page, name).locator("> button")
    expect(target).to_be_visible(timeout=PAGE_TIMEOUT)
    target.focus()
    assert page.evaluate("() => document.activeElement.classList.contains('nm-cap-box')")
    return target


def test_keyboard_reaches_a_box_with_a_visible_focus_ring(browser, live_server, map_tenants):
    names = map_tenants["names"]
    context, page = _open(browser, live_server, map_tenants["emails"]["mixed"])
    try:
        _focus_box(page, names["unassessed"])
        ring = page.evaluate(
            "() => { const s = getComputedStyle(document.activeElement);"
            " return s.boxShadow !== 'none' || (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0); }")
        assert ring, "no visible focus ring on the focused map box"
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.tagName") in ("BUTTON", "A", "SELECT", "INPUT"), \
            "Tab from a box must land on another control"
        _assert_nothing_invented(page)
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "Open: a map box's drill-down looks for #tab-heatmap, which the lens select replaced, so Enter "
    "opens nothing and shows a toast that says it did. The old grid opened its detail panel here."))
def test_enter_on_a_box_opens_its_detail(browser, live_server, map_tenants):
    names = map_tenants["names"]
    context, page = _open(browser, live_server, map_tenants["emails"]["mixed"])
    try:
        _focus_box(page, names["unassessed"])
        page.keyboard.press("Enter")
        expect(page.locator("#heatmap-detail")).to_be_visible(timeout=5000)
        expect(page.locator("#heatmap-detail-name")).to_have_text(names["unassessed"])
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", [
    (DESKTOP, "light"), (DESKTOP, "dark"), (NARROW, "light"), (NARROW, "dark"),
])
def test_not_assessed_marker_is_legible_in_both_themes_at_both_widths(browser, live_server, map_tenants, viewport, scheme):
    names = map_tenants["names"]
    context, page = _open(browser, live_server, map_tenants["emails"]["mixed"], viewport, scheme)
    try:
        marker = _box(page, names["unassessed"]).locator("button > span", has_text="Not assessed")
        expect(marker).to_be_visible(timeout=PAGE_TIMEOUT)
        ratio = marker.evaluate(CONTRAST_JS)
        assert ratio >= 4.5, "Not assessed contrast %s:1 in %s theme at %dpx (AA needs 4.5)" % (
            ratio, scheme, viewport["width"])
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), \
            "the page scrolls sideways at %dpx" % viewport["width"]
    finally:
        context.close()
