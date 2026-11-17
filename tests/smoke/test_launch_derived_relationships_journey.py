"""An enterprise architect keeps the indirect connections current after a model edit.

From an empty organisation: an application, the service it realises and the
team that service serves are created on the product's own screens, and the two
relationships through the interface the Composer draws them with. In the AI
chat the architect works out the indirect connections and sees the team reached
through a worked-out connection; the run is listed on the answer. Changing one of
the relationships marks that connection out of date and says why; working them
out again makes it current and records a second run. A second organisation sees
none of it.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, role_of
from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_launch_tech_radar_journey import _new_page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

RECOMPUTE = "/api/v1/intelligence/derivation/recompute"


def _create_application(page, base, name):
    assert page.goto(base + "/applications/", timeout=PAGE_TIMEOUT).status == 200
    page.get_by_test_id("btn-add-application").first.click()
    dialog = page.locator("#modal-create")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#ca-name").fill(name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        dialog.locator("button[type=submit]").click()


def _create_element(page, base, layer, element_type, name):
    page.goto(base + "/architecture/dashboard?layer=%s" % layer, wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    page.get_by_test_id("btn-create-element").click()
    modal = page.locator("#archimate-form-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.get_by_label("Element Type").select_option(element_type)
    modal.get_by_label("Element name").fill(name)
    with page.expect_response(
        lambda r: r.url.endswith("/architecture/%s/%s/new" % (layer, element_type))
        and r.request.method == "POST"
    ) as created:
        modal.get_by_role("button", name="Submit").click()
    assert created.value.status == 200, created.value.text()


def _element_ids(page, noun):
    status, body = api(page, "GET", "/archimate/api/elements/search?q=%s" % noun.replace(" ", "%20"))
    assert status == 200, body
    return {row["name"]: row["id"] for row in body["data"]}


def _ask_about(page, base, name):
    """Ask what breaks if *name* fails, in a freshly opened chat, and return the answer."""
    return ask(page, base, "What breaks if %s fails" % name)


def _work_them_out(answer):
    with answer.page.expect_response(
        lambda r: r.url.endswith(RECOMPUTE) and r.request.method == "POST"
    ) as done:
        answer.get_by_role("button", name="Work them out now").first.click()
    assert done.value.status == 200, done.value.text()


def _status(answer):
    return answer.locator("[data-derivation-status]")


def _derived_row(answer, team_name):
    return answer.locator('[data-answer-row][data-kind="derived"]').filter(
        has=answer.page.get_by_role("link", name=team_name, exact=True))


def test_indirect_connections_go_out_of_date_on_an_edit_and_are_worked_out_again(
    browser, live_server
):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    noun = "Tidewater %s" % uuid.uuid4().hex[:6]
    app_name = "%s Orders" % noun
    service_name = "%s Order Service" % noun
    team_name = "%s Fulfilment Team" % noun

    context, page = _new_page(browser)
    try:
        assert role_of(org["emails"]["enterprise_architect"]) == "enterprise_architect"
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _create_application(page, live_server, app_name)
        _create_element(page, live_server, "application", "ApplicationService", service_name)
        _create_element(page, live_server, "business", "BusinessActor", team_name)
        ids = _element_ids(page, noun)
        assert {app_name, service_name, team_name} <= set(ids), ids

        # Drawn in the Composer: its connect gesture posts to this interface.
        status, realises = api(page, "POST", "/archimate/api/relationships", {
            "source_element_id": ids[app_name], "target_element_id": ids[service_name],
            "relationship_type": "realization"})
        assert status == 201, realises
        status, serves = api(page, "POST", "/archimate/api/relationships", {
            "source_element_id": ids[service_name], "target_element_id": ids[team_name],
            "relationship_type": "serving"})
        assert status == 201, serves

        # Never worked out: the answer says so, with dashes rather than zeros.
        answer = _ask_about(page, live_server, app_name)
        expect(_status(answer).locator("[data-derivation-never-run]")).to_have_text("Not worked out yet.")
        for field in ("last-run", "current", "stale"):
            expect(_status(answer).locator("[data-derivation-%s]" % field)).to_have_text("—")

        # Work them out, and the team is reached through a worked-out connection.
        expect(answer.get_by_text("We haven't worked out the indirect connections for your model yet")).to_be_visible()
        _work_them_out(answer)
        answer = page.get_by_test_id("estate-answer").nth(0)
        expect(page.get_by_test_id("estate-answer")).to_have_count(1)
        expect(_derived_row(answer, team_name)).to_be_visible(timeout=PAGE_TIMEOUT)

        # The run is recorded and shown on the next answer.
        answer = _ask_about(page, live_server, app_name)
        expect(_status(answer).locator("[data-derivation-run]")).to_have_count(1)
        expect(_status(answer).locator("[data-derivation-run]").first).to_contain_text("A person")
        expect(_status(answer).locator("[data-derivation-last-run]")).not_to_have_text("—")
        expect(_status(answer).locator("[data-derivation-stale]")).to_have_text("0")
        current = int(_status(answer).locator("[data-derivation-current]").inner_text())
        assert current >= 1

        # Edit one of the relationships the way the Composer's edit panel does.
        status, body = api(page, "PUT", "/archimate/api/relationships/%s" % serves["id"],
                           {"description": "Now served through the partner desk"})
        assert status == 200, body
        answer = _ask_about(page, live_server, app_name)
        expect(_status(answer).locator("[data-derivation-stale]")).not_to_have_text("0")
        expect(_status(answer).locator("[data-derivation-stale-reasons]")).to_contain_text(
            "out of date because a relationship was changed")

        # The answer says it may be out of date; working them out again makes it current.
        expect(answer.locator("[data-stale-notice]")).to_be_visible(timeout=PAGE_TIMEOUT)
        _work_them_out(answer)
        answer = page.get_by_test_id("estate-answer").nth(0)
        expect(answer.locator("[data-stale-notice]")).to_have_count(0, timeout=PAGE_TIMEOUT)
        expect(_derived_row(answer, team_name)).to_be_visible(timeout=PAGE_TIMEOUT)

        answer = _ask_about(page, live_server, app_name)
        expect(_status(answer).locator("[data-derivation-run]")).to_have_count(2)
        expect(_status(answer).locator("[data-derivation-stale]")).to_have_text("0")
        expect(_status(answer).locator("[data-derivation-stale-reasons]")).to_have_count(0)
        team_id = ids[team_name]
        app_id = ids[app_name]
    finally:
        context.close()

    # Another organisation: none of it is visible, on the page or the interface.
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        other_app = "Other org app %s" % uuid.uuid4().hex[:6]
        _create_application(page, live_server, other_app)
        answer = _ask_about(page, live_server, other_app)
        expect(_status(answer).locator("[data-derivation-never-run]")).to_be_visible()
        expect(_status(answer).locator("[data-derivation-current]")).to_have_text("—")
        assert noun not in page.locator("main").inner_text()
        status, _body = api(page, "GET", "/api/v1/intelligence/impact/%s" % app_id)
        assert status == 404, (status, _body)
        status, body = api(page, "GET", "/archimate/api/elements/search?q=%s" % noun.replace(" ", "%20"))
        assert status == 200 and body["data"] == [], body
        assert team_id not in [row["id"] for row in body["data"]]
    finally:
        context.close()
