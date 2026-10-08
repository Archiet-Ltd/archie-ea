"""A transformation lead keeps the stakeholder map for a transformation programme.

From an empty organisation: a programme is created with the new-programme
wizard, and its overview links to the stakeholder map opened on that
programme. The map says honestly that the programme records nothing it
affects yet, so there are no owners to suggest. Stakeholders are added with
their influence and interest, one is moved to another quadrant, and after a
reload the map still shows them on the programme, in those quadrants. A second
organisation can neither open that programme's map nor read its stakeholders.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_launch_tech_radar_journey import _create_programme, _new_page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _add_stakeholder(page, name, influence, interest, *, directory_term=None):
    page.get_by_role("button", name="Add Stakeholder", exact=True).first.click()
    modal = page.locator("#add-stakeholder-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    search = modal.get_by_label("Person or Team", exact=True)
    if directory_term:
        # A person already in the directory: picked from the search results.
        search.press_sequentially(directory_term, delay=20)
        modal.get_by_text(name, exact=True).first.click()
    else:
        search.fill(name)
        modal.get_by_label("Person name", exact=True).fill(name)
    modal.get_by_label("Influence level").fill(str(influence))
    modal.get_by_label("Interest level").fill(str(interest))
    with page.expect_response(
        lambda r: r.url.endswith("/api/stakeholders/") and r.request.method == "POST"
    ) as created:
        modal.get_by_role("button", name="Add Stakeholder", exact=True).click()
    assert created.value.status == 201, created.value.text()
    expect(modal).to_be_hidden(timeout=PAGE_TIMEOUT)


def _row(page, name):
    return page.get_by_test_id("stakeholder-table").locator("tr[data-stakeholder='%s']" % name)


def test_programme_stakeholders_are_added_positioned_and_kept(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    programme = "Operating Model Programme %s" % tag
    sponsor = "Board Sponsor %s" % tag
    lead_name = "Launch enterprise_a"

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _create_programme(page, live_server, programme)
        programme_id = int(re.search(r"/programmes/(\d+)/", page.url).group(1))

        # From the programme's own overview to its stakeholder map.
        page.goto(live_server + "/solutions/programmes/%d/overview" % programme_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("programme-stakeholder-map-link").click()
        assert "/stakeholders/map?programme_id=%d" % programme_id in page.url
        expect(page.get_by_label("Select a programme...")).to_have_value(str(programme_id))
        expect(page.get_by_text("No stakeholders yet")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("programme-suggestions-none")).to_have_text(
            "This programme records no affected capabilities yet, so there are no owners to suggest.")

        _add_stakeholder(page, sponsor, 5, 5)
        _add_stakeholder(page, lead_name, 2, 4, directory_term="Launch")

        expect(_row(page, sponsor).locator("[data-quadrant]")).to_have_text("Manage closely")
        expect(_row(page, lead_name).locator("[data-quadrant]")).to_have_text("Keep informed")

        # Move the lead up the influence axis without dragging.
        _row(page, lead_name).get_by_label("Influence of %s" % lead_name).select_option("5")
        with page.expect_response(lambda r: "/api/stakeholders/" in r.url and r.request.method == "PATCH") as moved:
            _row(page, lead_name).get_by_role("button", name="Save position").click()
        assert moved.value.status == 200, moved.value.text()
        expect(_row(page, lead_name).locator("[data-quadrant]")).to_have_text("Manage closely")

        # After a reload the map still holds both, on this programme, where they were put.
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_label("Select a programme...")).to_have_value(str(programme_id))
        expect(page.locator("#stk-svg .stk-node")).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(_row(page, sponsor).locator("[data-quadrant]")).to_have_text("Manage closely")
        expect(_row(page, lead_name).locator("[data-quadrant]")).to_have_text("Manage closely")
        expect(_row(page, lead_name).get_by_label("Interest of %s" % lead_name)).to_have_value("4")
        status, body = api(page, "GET", "/api/stakeholders/map-data?programme_id=%d" % programme_id)
        assert status == 200
        assert sorted(s["name"] for s in body) == sorted([sponsor, lead_name])
    finally:
        context.close()

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        page.goto(live_server + "/stakeholders/map?programme_id=%d" % programme_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_label("Select a programme...")).to_have_value("")
        expect(page.get_by_text(programme)).to_have_count(0)
        expect(page.get_by_text(sponsor)).to_have_count(0)
        status, _body = api(page, "GET", "/api/stakeholders/map-data?programme_id=%d" % programme_id)
        assert status == 404
        status, _body = api(page, "POST", "/api/stakeholders/", {
            "name": "Intruder %s" % tag, "programme_id": programme_id})
        assert status == 404
    finally:
        context.close()
