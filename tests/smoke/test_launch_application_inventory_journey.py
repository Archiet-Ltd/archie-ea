"""An application manager registers an application; an architect lists its consumers.

The application manager of a brand-new organisation registers a newly
purchased application from the portfolio (/applications/) through the "Add
Application" dialog. After a reload it is in the list; registering it again
under a different letter case is refused inside the dialog and the list still
holds exactly one. Two more applications follow: B consumes A and C consumes
B, related in the model through the same interface the model composer's
canvas calls when a serving relationship is drawn.

Before changing A's interface, the architect opens A's impact page and reads
the "Consumers (depend on this)" list: B directly, C indirectly through two
hops; on C's page the "Providers (this depends on)" list reads the reverse.
Before this list existed the page only drew a graph - and read every arrow's
source as the dependent, so "A serves B" was shown as A depending on B. After
a reload the lists are unchanged. A second new organisation sees none of it.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _open_portfolio(page, base):
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _submit_new_application(page, name):
    page.get_by_test_id("btn-add-application").click()
    dialog = page.locator("#modal-create")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#ca-name").fill(name)
    with page.expect_response(
        lambda r: r.url.endswith("/applications/create") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as created:
        dialog.get_by_role("button", name="Add Application", exact=True).click()
    return created.value, dialog


def _register(page, base, name):
    _open_portfolio(page, base)
    # A successful registration closes the dialog and moves the page on.
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        response, _dialog = _submit_new_application(page, name)
    assert response.status in (200, 201), "registering %r answered %s" % (name, response.status)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _rows(page, name):
    return page.get_by_test_id("applications-table").locator("tbody tr").filter(
        has_text=re.compile(re.escape(name), re.IGNORECASE))


def _element_id(page, name):
    """The application's model element, found the way the composer's search panel finds it."""
    status, body = api(page, "GET", "/archimate/api/elements/search?type=ApplicationComponent&q=%s" % name)
    assert status == 200, body
    ids = [e["id"] for e in body["data"] if e["name"] == name]
    assert len(ids) == 1, "expected one model element named %r, got %r" % (name, body)
    return ids[0]


def _serves(page, provider_id, consumer_id):
    # Drawing a serving connector on the composer canvas posts exactly this.
    status, body = api(page, "POST", "/archimate/api/relationships", {
        "source_element_id": provider_id, "target_element_id": consumer_id,
        "relationship_type": "serving"})
    assert status in (200, 201), "relating %s -> %s answered %s: %r" % (provider_id, consumer_id, status, body)


def _list_entries(page, key):
    section = page.get_by_test_id("impact-" + key)
    expect(section.locator("#impact-%s-status" % key)).not_to_have_text("Loading…", timeout=PAGE_TIMEOUT)
    entries = {}
    for item in section.locator("li").all():
        entries[item.locator("a").inner_text().strip()] = item.locator("span").last.inner_text().strip()
    return entries


def _open_impact(page, base, element_id):
    page.goto(base + "/archimate/elements/%d/impact" % element_id, wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)


def test_register_an_application_and_list_everything_that_consumes_it(browser, live_server):
    first = create_fresh_org("application_manager")
    second = create_fresh_org("application_manager")
    suffix = first["suffix"]
    app_a = "Launch Ledger %s" % suffix
    app_b = "Launch Billing %s" % suffix
    app_c = "Launch Portal %s" % suffix

    context, page = _new_page(browser)
    sign_in(page, live_server, first["emails"]["application_manager"])
    _register(page, live_server, app_a)
    _open_portfolio(page, live_server)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_rows(page, app_a)).to_have_count(1, timeout=PAGE_TIMEOUT)

    # The same application again, in another letter case, is refused in the dialog.
    response, dialog = _submit_new_application(page, app_a.upper())
    assert response.status == 409, "a duplicate registration answered %s" % response.status
    assert response.json().get("code") == "DUPLICATE_NAME"
    expect(dialog.get_by_role("alert").filter(has_text="already exists")).to_be_visible(timeout=PAGE_TIMEOUT)
    _open_portfolio(page, live_server)
    expect(_rows(page, app_a)).to_have_count(1, timeout=PAGE_TIMEOUT)

    _register(page, live_server, app_b)
    _register(page, live_server, app_c)
    a_id, b_id, c_id = (_element_id(page, n) for n in (app_a, app_b, app_c))
    _serves(page, a_id, b_id)
    _serves(page, b_id, c_id)

    # A's consumers, before and after a reload.
    _open_impact(page, live_server, a_id)
    expected_consumers = {app_b: "direct", app_c: "indirect (2 hops)"}
    assert _list_entries(page, "consumers") == expected_consumers
    assert _list_entries(page, "providers") == {}
    expect(page.locator("#impact-providers-status")).to_have_text("None recorded within 2 hops.")
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert _list_entries(page, "consumers") == expected_consumers

    # C's providers read the chain the other way.
    page.get_by_test_id("impact-consumers").get_by_role("link", name=app_c).click()
    page.wait_for_url(re.compile(r"/archimate/elements/%d/impact$" % c_id), timeout=PAGE_TIMEOUT)
    assert _list_entries(page, "providers") == {app_b: "direct", app_a: "indirect (2 hops)"}
    assert _list_entries(page, "consumers") == {}
    context.close()

    # A second organisation sees none of it.
    context, page = _new_page(browser)
    sign_in(page, live_server, second["emails"]["application_manager"])
    _open_portfolio(page, live_server)
    expect(_rows(page, app_a)).to_have_count(0)
    response = page.goto(live_server + "/archimate/elements/%d/impact" % a_id,
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404, "another organisation's impact page answered %s" % response.status
    status, _ = api(page, "GET", "/archimate/api/element/%d/impact-graph" % a_id)
    assert status == 404
    context.close()
