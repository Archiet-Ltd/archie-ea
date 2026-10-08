"""Value streams at risk, in a real browser.

A business architect opens the page from their own sidebar and sees the
organisation's value streams whose capabilities fall below the maturity
threshold. The data is seeded through the product's own routes -- the
capability create endpoint, the value stream and stage forms and the
capability-to-stage mapping endpoint -- so the page is proved against rows a
person could have made, not against rows written behind the product's back.

What is asserted:

* the sidebar entry reaches the page, and the page's rows and counts are the
  intelligence API's own rows and counts for the same threshold;
* changing the threshold asks the API again, puts the value in the address
  bar, and a reload opens on the same state;
* who reaches the page: every signed-in archetype opens it (the route and the
  API it reads are guarded by sign-in only, and every answer is scoped to the
  caller's organisation); only the business architect has it in the sidebar;
  a visitor who is not signed in is sent to sign in;
* the error, nothing-at-risk and nothing-linked states;
* screenshots at desktop and phone width, light and dark, with and without
  data (``SMOKE_SCREENSHOT_DIR`` sets where they go).
"""

import json
import os
import pathlib
import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from .conftest import ARCHETYPES, PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE_PATH = "/intelligence/value-streams-at-risk"
API_PATH = "/api/v1/intelligence/value-streams-at-risk"
SIDEBAR_LABEL = "Value Streams at Risk"


def _assert_path_and_threshold(page, path, threshold):
    parsed = urlparse(page.url)
    assert parsed.path == path
    assert parse_qs(parsed.query).get("threshold") == [str(threshold)]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass


def _csrf(page):
    return page.locator('meta[name="csrf-token"]').get_attribute("content") or ""


def _ok(resp, what):
    assert resp.ok, "%s failed: %s %s" % (what, resp.status, resp.text()[:300])
    return resp


def _create_capability(page, base, name, current, target):
    _ok(page.request.post(
        base + "/enterprise/capabilities",
        data=json.dumps({"name": name, "type": "strategic",
                         "current_maturity_level": current, "target_maturity_level": target}),
        headers={"Content-Type": "application/json", "X-CSRFToken": _csrf(page)},
    ), "create capability %s" % name)


def _create_value_stream(page, base, name):
    resp = _ok(page.request.post(
        base + "/value-streams/create",
        form={"name": name, "csrf_token": _csrf(page)},
    ), "create value stream %s" % name)
    match = re.search(r"/value-streams/(\d+)", resp.url)
    assert match, "value stream create did not land on its page: %s" % resp.url
    return int(match.group(1))


def _create_stage(page, base, vs_id, name, order):
    _ok(page.request.post(
        base + "/value-streams/%d/stages" % vs_id,
        form={"name": name, "stage_order": str(order), "csrf_token": _csrf(page)},
    ), "create stage %s" % name)


def _stage_ids(page, base, vs_id):
    grid = _ok(page.request.get(base + "/value-streams/%d/grid" % vs_id), "read grid").json()
    return {s["name"]: s["id"] for s in grid["stages"]}


def _unified_capability_id(page, base, vs_id, name):
    found = _ok(page.request.get(
        base + "/value-streams/%d/api/unmapped-capabilities" % vs_id, params={"q": name}
    ), "find capability %s" % name).json()["capabilities"]
    ids = [c["id"] for c in found if c["name"] == name]
    assert len(ids) == 1, "capability %s is not in the value stream picker: %r" % (name, found)
    return ids[0]


def _link(page, base, cap_id, vs_id, stage_id):
    _ok(page.request.post(
        base + "/value-streams/api/mapping",
        data=json.dumps({"capability_id": cap_id, "value_stream_id": vs_id,
                         "value_stream_stage_id": stage_id, "support_type": "primary",
                         "support_level": 4, "impact_level": "high"}),
        headers={"Content-Type": "application/json", "X-CSRFToken": _csrf(page)},
    ), "link capability %s" % cap_id)


def _fresh_business_architect(label):
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    safe = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    email = "%s.%s@example.com" % (safe, suffix)
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="%s Org %s" % (label, suffix), slug="%s-%s" % (safe, suffix))
        db.session.add(org)
        db.session.commit()
        user = User(
            email=email,
            first_name="Fresh",
            last_name="Architect",
            organization_id=org.id,
            enterprise_role="business_architect",
            confirmed=True,
        )
        db.session.add(user)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.commit()
        return {"email": email, "org_name": org.name}


@pytest.fixture(scope="module")
def vsr_seed(browser, live_server, seeded):
    """Two value streams made through the product's own routes: Onboarding
    depends on a capability at maturity 1 (on two stages) and on one with no
    maturity recorded; Settlement depends only on a capability at maturity 4."""
    suffix = uuid.uuid4().hex[:6]
    names = {
        "onboard": "Onboard customer %s" % suffix,
        "settle": "Settle claim %s" % suffix,
        "weak": "Identity checks %s" % suffix,
        "unknown": "Document capture %s" % suffix,
        "strong": "Payments %s" % suffix,
    }
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(live_server + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _create_capability(page, live_server, names["weak"], 1, 4)
        _create_capability(page, live_server, names["unknown"], None, None)
        _create_capability(page, live_server, names["strong"], 4, 4)
        onboard = _create_value_stream(page, live_server, names["onboard"])
        settle = _create_value_stream(page, live_server, names["settle"])
        _create_stage(page, live_server, onboard, "Apply", 1)
        _create_stage(page, live_server, onboard, "Verify", 2)
        _create_stage(page, live_server, settle, "Pay", 1)
        onboard_stages = _stage_ids(page, live_server, onboard)
        settle_stages = _stage_ids(page, live_server, settle)
        weak = _unified_capability_id(page, live_server, onboard, names["weak"])
        unknown = _unified_capability_id(page, live_server, onboard, names["unknown"])
        strong = _unified_capability_id(page, live_server, settle, names["strong"])
        _link(page, live_server, weak, onboard, onboard_stages["Apply"])
        _link(page, live_server, weak, onboard, onboard_stages["Verify"])
        _link(page, live_server, unknown, onboard, onboard_stages["Apply"])
        _link(page, live_server, strong, settle, settle_stages["Pay"])
    finally:
        ctx.close()
    return {"names": names, "onboard": onboard, "settle": settle}


def _api(page, base, threshold):
    resp = _ok(page.request.get(base + API_PATH, params={"threshold": str(threshold)}), "read API")
    return resp.json()["data"]


def _wait_for_rows(page):
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-vsr-page]');"
        " return !!(el && el._x_dataStack && el._x_dataStack[0].state !== 'loading'); }",
        timeout=PAGE_TIMEOUT,
    )


def _dom_rows(page):
    return page.eval_on_selector_all(
        "[data-vsr-row]",
        "els => els.map(e => ({id: Number(e.getAttribute('data-vsr-row-id')),"
        " name: e.querySelector('[data-vsr-name]').textContent.trim(),"
        " atRisk: Number(e.querySelector('[data-vsr-at-risk-count]').textContent.trim())}))",
    )


def _assert_page_agrees_with_api(page, base, threshold):
    answer = _api(page, base, threshold)
    rows = _dom_rows(page)
    assert rows == [
        {"id": r["value_stream"]["id"], "name": r["value_stream"]["name"],
         "atRisk": r["at_risk_capability_count"]}
        for r in answer["rows"]
    ]
    summary = page.locator("[data-vsr-summary]")
    s = answer["summary"]
    expect(summary).to_contain_text(
        "%d of %d value streams depend on a capability below maturity %d."
        % (s["value_streams_at_risk"], s["value_streams_considered"], threshold)
    )
    return answer


def _row(page, vs_id):
    return page.locator('[data-vsr-row][data-vsr-row-id="%d"]' % vs_id)


def _threshold(page, level):
    return page.locator("label", has=page.locator("#vsr-threshold-%d" % level))


def test_business_architect_reaches_the_page_from_the_sidebar_and_sees_the_apis_rows(
    browser, live_server, seeded, vsr_seed
):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.get_by_test_id("sidebar").get_by_role("link", name=SIDEBAR_LABEL, exact=True)
        assert link.count() == 1
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            link.click()
        _assert_path_and_threshold(page, PAGE_PATH, 3)
        expect(page.get_by_role("heading", level=1, name="Value streams at risk")).to_be_visible()
        _wait_for_rows(page)

        _assert_page_agrees_with_api(page, live_server, 3)
        onboard = _row(page, vsr_seed["onboard"])
        settle = _row(page, vsr_seed["settle"])
        expect(onboard.locator("[data-vsr-at-risk-count]")).to_have_text("1")
        expect(settle.locator("[data-vsr-at-risk-count]")).to_have_text("0")
        expect(onboard).to_contain_text("At risk")

        # Expand the row: its capabilities, with the unknown maturity as a dash.
        toggle = onboard.locator("[data-vsr-toggle]")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        caps = onboard.locator("[data-vsr-capability]")
        expect(caps).to_have_count(2)
        weak = caps.filter(has_text=vsr_seed["names"]["weak"])
        expect(weak).to_contain_text("Below threshold")
        expect(weak).to_contain_text("Apply, Verify")
        expect(weak.locator("[data-vsr-current]")).to_have_text("1")
        unknown = caps.filter(has_text=vsr_seed["names"]["unknown"])
        expect(unknown).to_contain_text("Not assessed")
        expect(unknown.locator("[data-vsr-current]")).to_have_text("—")

        # Change the threshold: the API is asked again and the address bar
        # carries the new value.
        with page.expect_response(
            lambda r: API_PATH in r.url and "threshold=5" in r.url, timeout=PAGE_TIMEOUT
        ):
            _threshold(page, 5).click()
        _wait_for_rows(page)
        assert "threshold=5" in page.url
        _assert_page_agrees_with_api(page, live_server, 5)
        expect(_row(page, vsr_seed["settle"]).locator("[data-vsr-at-risk-count]")).to_have_text("1")

        # Reload: the page opens on the same threshold and the same rows.
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)
        expect(page.locator("#vsr-threshold-5")).to_be_checked()
        _assert_page_agrees_with_api(page, live_server, 5)
        expect(_row(page, vsr_seed["settle"]).locator("[data-vsr-at-risk-count]")).to_have_text("1")
    finally:
        ctx.close()


def test_at_threshold_one_nothing_is_below_and_the_page_says_so(
    browser, live_server, seeded, vsr_seed
):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(live_server + PAGE_PATH + "?threshold=1", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)
        expect(page.locator("[data-vsr-none-at-risk]")).to_have_text(
            "No value streams are below the maturity threshold."
        )
        _assert_page_agrees_with_api(page, live_server, 1)
    finally:
        ctx.close()


def test_a_failed_answer_is_shown_as_an_error_never_as_zero(browser, live_server, seeded):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.route(
            re.compile(r".*/api/v1/intelligence/value-streams-at-risk.*"),
            lambda route: route.fulfill(
                status=500, content_type="application/json",
                body=json.dumps({"success": False, "error": {"code": "ERROR", "message": "Server fault."}}),
            ),
        )
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)
        alert = page.locator("[data-vsr-error]")
        expect(alert).to_be_visible()
        expect(alert).to_contain_text("We could not load the value streams just now. Server fault.")
        assert page.locator("[data-vsr-row]").count() == 0
        assert page.locator("[data-vsr-summary]").count() == 0
    finally:
        ctx.close()


_NOTHING_LINKED = {
    "success": True,
    "data": {
        "threshold": 3,
        "threshold_basis": "current_maturity_level < threshold",
        "rows": [],
        "summary": {"value_streams_considered": 0, "value_streams_at_risk": 0,
                    "capabilities_considered": 0, "capabilities_below_threshold": 0,
                    "capabilities_with_no_maturity": 0, "value_streams_not_linked_to_model": 0},
        "reasons": ["no_value_stream_recorded"],
    },
}


def _answer_nothing_linked(page):
    page.route(
        re.compile(r".*/api/v1/intelligence/value-streams-at-risk.*"),
        lambda route: route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps(_NOTHING_LINKED)),
    )


def test_an_organisation_with_nothing_linked_is_sent_to_where_links_are_made(
    browser, live_server, seeded
):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        _answer_nothing_linked(page)
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)
        state = page.locator("[data-vsr-unmapped]")
        expect(state).to_contain_text("No capabilities are linked to a value stream yet")
        cta = state.get_by_role("link", name="Link capabilities to value streams")
        expect(cta).to_have_attribute("href", "/value-streams/")
        assert page.locator("[data-vsr-row]").count() == 0
    finally:
        ctx.close()


def test_a_fresh_real_organisation_with_no_data_shows_the_real_empty_state(browser, live_server):
    fresh = _fresh_business_architect("Value Streams At Risk Empty")
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, fresh["email"])
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)
        state = page.locator("[data-vsr-unmapped]")
        expect(state).to_contain_text("No capabilities are linked to a value stream yet")
        expect(page.locator("[data-vsr-error]")).to_have_count(0)
        assert page.locator("[data-vsr-row]").count() == 0
    finally:
        ctx.close()


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_who_reaches_the_page_and_whose_sidebar_carries_it(browser, live_server, seeded, archetype):
    """Every signed-in archetype opens the page (sign-in is the route's only
    guard, as it is the API's, and the answer is scoped to the caller's own
    organisation). Only the business architect has it in the sidebar."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"][archetype])
        response = page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        _assert_path_and_threshold(page, PAGE_PATH, 3)
        expect(page.get_by_role("heading", level=1, name="Value streams at risk")).to_be_visible()
        links = page.get_by_test_id("sidebar").get_by_role("link", name=SIDEBAR_LABEL, exact=True)
        assert links.count() == (1 if archetype == "business_architect" else 0)
    finally:
        ctx.close()


def test_a_visitor_who_is_not_signed_in_is_sent_to_sign_in(browser, live_server):
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/account/login" in page.url
    finally:
        ctx.close()


# --- screenshots ------------------------------------------------------------

VIEWPORTS = {"desktop": (1280, 900), "phone": (360, 800)}
THEMES = ("light", "dark")


def _shot_dir():
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "value-streams-at-risk"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("data", ["with-data", "without-data"])
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_capture_value_streams_at_risk(browser, live_server, seeded, vsr_seed, viewport, theme, data):
    width, height = VIEWPORTS[viewport]
    # The product's own theme rule: a stored choice, else the system setting.
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        if data == "without-data":
            _answer_nothing_linked(page)
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)
        if data == "with-data":
            _row(page, vsr_seed["onboard"]).locator("[data-vsr-toggle]").click()
            expect(_row(page, vsr_seed["onboard"]).locator("[data-vsr-capability]")).to_have_count(2)
        # Nothing on the page may scroll sideways at phone width.
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, "the page scrolls sideways by %dpx" % overflow
        page.wait_for_timeout(300)
        out = _shot_dir() / ("value-streams-at-risk-%s-%s-%s.png" % (data, viewport, theme))
        page.screenshot(path=str(out), full_page=True)
        assert out.exists() and out.stat().st_size > 0
        print("[screenshot] %s" % out)
    finally:
        ctx.close()
