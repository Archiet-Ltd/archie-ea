"""Application architects model a component landscape in the ArchiMate Composer,
and a solution architect asks the AI chat why two things are connected.

Composer: a fresh organisation. Elements are added through the element
palette (drag onto the canvas, then name a new one or pick an existing one
from the live search), relationships are drawn in connect mode through the
relationship-type picker, a connection the metamodel forbids is refused with
the reason and the allowed types, and the view is saved, reopened from the
diagrams library and checked element by element.

Twin map: in a fresh organisation, a chain of serving relationships drawn in
the Composer; the map is asked to work out the indirect connections, and the
explanation of the worked-out one names every element on the path. Both are
checked again after a reload, and a second organisation reaches none of it.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, role_of
from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1920, "height": 1080})
    # A first sign-in shows the one-time welcome tour over every page; this
    # person has already been through it.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _open_composer(page, base, query=""):
    page.goto(base + "/archimate/composer" + query, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-testid='diagram-palette']")).to_be_visible(timeout=PAGE_TIMEOUT)


# The palette names every type in full; these tests speak of the two they use by their short names.
_PALETTE_TYPE = {"Application": "ApplicationComponent", "App Interface": "ApplicationInterface"}


def _drop_from_palette(page, label, x, y):
    """Drag an element type from the palette onto the canvas; the add-element search opens.

    The empty canvas shows starter-template cards across the middle of its
    upper half, so drops land in the lower half or out at the sides."""
    chip = page.locator("[data-testid='palette-item-%s']" % _PALETTE_TYPE[label])
    chip.drag_to(page.locator("#composer-canvas"), target_position={"x": x, "y": y})
    overlay = page.locator("#search-overlay")
    expect(overlay).to_be_visible(timeout=PAGE_TIMEOUT)
    return overlay


def _add_new(page, label, name, x, y):
    overlay = _drop_from_palette(page, label, x, y)
    field = overlay.get_by_label("New Element Name")
    expect(field).to_be_visible(timeout=PAGE_TIMEOUT)
    field.click()
    field.press_sequentially(name, delay=15)
    create = overlay.locator(".search-create-btn")
    expect(create).to_be_enabled(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: "/api/architecture-assistant/create-element" in r.url and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        create.click()
    assert resp.value.status < 400, "element create failed: %d %s" % (resp.value.status, resp.value.text()[:300])
    expect(_node(page, name)).to_be_visible(timeout=PAGE_TIMEOUT)


def _add_existing(page, label, name, x, y):
    overlay = _drop_from_palette(page, label, x, y)
    overlay.get_by_label("Search Query").fill(name.split(" ")[0])
    result = overlay.locator(".search-result-item", has_text=name)
    expect(result).to_be_visible(timeout=PAGE_TIMEOUT)
    result.click()
    expect(_node(page, name)).to_be_visible(timeout=PAGE_TIMEOUT)


def _node(page, name):
    return page.locator(".joint-element", has_text=name)


def _picker(page):
    return page.locator("[role=dialog][aria-label='Select relationship type']")


def _connect(page, source, target):
    """Connect mode (C), click the source then the target; the type picker opens.

    Connect mode stays on after a connection is made, and C toggles it, so
    Escape leaves it first and every connection starts from the same state."""
    page.keyboard.press("Escape")
    page.locator("#composer-canvas").click(position={"x": 5, "y": 5})
    page.keyboard.press("c")
    _node(page, source).click()
    _node(page, target).click()
    picker = _picker(page)
    expect(picker).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(picker.locator(".rel-picker-item").first).to_be_visible(timeout=PAGE_TIMEOUT)
    return picker


def _pick_type(page, picker, rel_type, expect_status=201):
    with page.expect_response(
        lambda r: r.url.endswith("/archimate/api/relationships") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        picker.locator("div.rel-picker-item").filter(
            has=page.locator("span", has_text=re.compile("^%s$" % rel_type, re.I))).first.click()
    assert resp.value.status == expect_status, "%s: %d %s" % (rel_type, resp.value.status, resp.value.text()[:300])
    return resp.value


def test_application_architect_models_components_and_interfaces(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    order = "Ordering %s" % tag
    billing = "Billing %s" % tag
    payments_api = "Payapi %s" % tag
    view_name = "Order landscape %s" % tag

    org1 = create_fresh_org("solution_architect")
    org2 = create_fresh_org("solution_architect")
    context, page = _new_page(browser)
    sign_in(page, base, org1["emails"]["solution_architect"])

    # An interface already in the organisation's catalogue, made in an earlier
    # Composer session, to be picked through live search below.
    _open_composer(page, base)
    _add_new(page, "App Interface", payments_api, 1250, 700)

    _open_composer(page, base)
    expect(_node(page, payments_api)).to_have_count(0)
    _add_new(page, "Application", order, 250, 700)
    _add_new(page, "Application", billing, 1100, 700)
    _add_existing(page, "App Interface", payments_api, 1250, 400)
    expect(page.locator(".joint-element")).to_have_count(3, timeout=PAGE_TIMEOUT)

    # Ordering passes order data to Billing (flow); the interface serves Billing.
    picker = _connect(page, order, billing)
    picker.get_by_label("Flow label (what is transferred)").fill("Order data")
    _pick_type(page, picker, "flow")
    expect(picker).to_be_hidden(timeout=PAGE_TIMEOUT)
    picker = _connect(page, payments_api, billing)
    _pick_type(page, picker, "serving")
    expect(page.locator(".joint-link")).to_have_count(2, timeout=PAGE_TIMEOUT)

    # A component cannot realise an interface: refused, with the allowed types named.
    picker = _connect(page, order, payments_api)
    invalid = picker.locator("[data-invalid-rel-type]", has_text=re.compile("^realization$", re.I))
    expect(invalid).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: r.url.endswith("/archimate/api/relationships") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        invalid.click()
    assert resp.value.status == 400, resp.value.status
    body = resp.value.json()
    assert "realization" not in body["valid_types"] and "serving" in body["valid_types"], body
    rejection = picker.locator("[data-rel-rejection]")
    expect(rejection).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(rejection).to_contain_text("does not allow realization")
    expect(rejection).to_contain_text(order)
    expect(rejection.locator("[data-rel-suggestions]")).to_contain_text(re.compile("serving", re.I))
    page.keyboard.press("Escape")
    expect(picker).to_be_hidden(timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-link")).to_have_count(2, timeout=PAGE_TIMEOUT)

    # Save the view.
    page.locator("#composer-canvas").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+s")
    name_box = page.locator('[aria-label="Viewpoint name"]')
    expect(name_box).to_be_visible(timeout=PAGE_TIMEOUT)
    name_box.fill(view_name)
    with page.expect_response(
        lambda r: "/archimate/api/saved-viewpoints" in r.url and r.request.method in ("POST", "PUT"),
        timeout=PAGE_TIMEOUT,
    ) as resp:
        page.get_by_role("button", name=re.compile("^Save$")).click()
    assert resp.value.status < 400, resp.value.text()[:300]
    expect(name_box).to_be_hidden(timeout=PAGE_TIMEOUT)

    # Reopen it from the diagrams library, in a fresh load.
    page.goto(base + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("tbody tr", has_text=view_name)
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("3")  # elements on the saved view
    row.get_by_role("link", name="Open").click()
    page.wait_for_url(re.compile(r"/archimate/composer\?.*viewpoint_id=\d+"), timeout=PAGE_TIMEOUT)
    diagram_url = page.url
    for name in (order, billing, payments_api):
        expect(_node(page, name)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-element")).to_have_count(3, timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-link")).to_have_count(2, timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-link", has_text="Order data")).to_have_count(1)
    expect(page.locator(".joint-link", has_text=re.compile("serving", re.I))).to_have_count(1)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-element")).to_have_count(3, timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-link")).to_have_count(2, timeout=PAGE_TIMEOUT)
    vp_id = int(re.search(r"viewpoint_id=(\d+)", diagram_url).group(1))
    context.close()

    # A second organisation sees none of it.
    context2, page2 = _new_page(browser)
    sign_in(page2, base, org2["emails"]["solution_architect"])
    page2.goto(base + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.locator("body")).not_to_contain_text(view_name)
    status, _ = api(page2, "GET", "/archimate/api/saved-viewpoints/%d" % vp_id)
    assert status == 404, "other org read the saved view: %s" % status
    status, found = api(page2, "GET", "/archimate/api/elements/search?limit=10&q=" + tag)
    assert status == 200, status
    assert tag not in str(found), "other org found this org's elements: %r" % (found,)
    context2.close()


def test_solution_architect_asks_why_two_things_are_connected(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    portal = "Storefront %s" % tag
    checkout = "Checkout %s" % tag
    ledger = "Ledger %s" % tag

    org1 = create_fresh_org("solution_architect")
    org2 = create_fresh_org("solution_architect")
    assert role_of(org1["emails"]["solution_architect"]) == "solution_architect"
    context, page = _new_page(browser)
    sign_in(page, base, org1["emails"]["solution_architect"])

    # Ledger serves Checkout, Checkout serves Storefront -- drawn in the Composer.
    # Nobody draws Ledger -> Storefront.
    _open_composer(page, base)
    _add_new(page, "Application", ledger, 250, 700)
    _add_new(page, "Application", checkout, 600, 700)
    _add_new(page, "Application", portal, 950, 700)
    _pick_type(page, _connect(page, ledger, checkout), "serving")
    _pick_type(page, _connect(page, checkout, portal), "serving")
    expect(page.locator(".joint-link")).to_have_count(2, timeout=PAGE_TIMEOUT)

    # Ask the chat what breaks if the Ledger fails; the Why? explanation of a worked-out
    # connection is on its card.
    status, found = api(page, "GET", "/archimate/api/elements/search?limit=10&q=" + ledger.replace(" ", "%20"))
    assert status == 200, found
    ledger_id = next(row["id"] for row in found["data"] if row["name"] == ledger)
    question = "What breaks if %s fails" % ledger
    answer = ask(page, base, question)

    # Nothing worked out yet in a new organisation: the answer says so and offers to.
    recompute = answer.get_by_role("button", name="Work them out now")
    expect(recompute).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: "/api/v1/intelligence/derivation/recompute" in r.url, timeout=PAGE_TIMEOUT,
    ) as resp:
        recompute.click()
    assert resp.value.status < 400, resp.value.text()[:300]

    def _derived_row(card):
        return card.locator("[data-slot='card']").filter(
            has=page.locator("[data-answer-row][data-kind=derived]")
        ).filter(has=page.get_by_role("link", name=portal, exact=True))

    answer = page.get_by_test_id("estate-answer").nth(0)
    expect(_derived_row(answer)).to_have_count(1, timeout=PAGE_TIMEOUT)

    def _explain_and_check(card):
        row = _derived_row(card)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(row.locator("[data-why-rule]")).to_contain_text("serving link followed by a serving link")
        chain = row.locator("[data-why-link]")
        expect(chain).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(chain.nth(0)).to_contain_text(ledger)
        expect(chain.nth(0)).to_contain_text(checkout)
        expect(chain.nth(1)).to_contain_text(checkout)
        expect(chain.nth(1)).to_contain_text(portal)

    _explain_and_check(answer)
    status, impact = api(page, "GET", "/api/v1/intelligence/impact/%d?include_derived=true" % ledger_id)
    assert status == 200, impact
    derived_ids = {
        r["relation"]["derived_id"] for r in (impact.get("data") or impact)["rows"]
        if r["relation"]["kind"] == "derived"
    }
    assert len(derived_ids) == 1
    derived_id = derived_ids.pop()

    # Asking again in a freshly opened chat gives the same explanation.
    _explain_and_check(ask(page, base, question))
    context.close()

    # A second organisation reaches neither the connection nor its explanation.
    context2, page2 = _new_page(browser)
    sign_in(page2, base, org2["emails"]["solution_architect"])
    status, _ = api(page2, "GET", "/api/v1/intelligence/derived/%d" % derived_id)
    assert status == 404, "other org read the explanation: %s" % status
    status, body = api(page2, "GET", "/api/v1/intelligence/impact/%d?include_derived=true" % ledger_id)
    assert status in (403, 404) or ledger not in str(body), "other org read the map: %s %r" % (status, body)
    context2.close()
