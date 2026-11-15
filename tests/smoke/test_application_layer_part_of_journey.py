"""Journey, in a browser: the architecture dashboard's application layer says what each row is part of.

An architect opens the application layer: "Alerts Gateway" reads "Part of" the application that composes
it, and the name is a link to that application's page, which lists its parts; "Alerts gateway and
consumers" reads "Not part of an application".
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_application_kind_journey import ARCHITECT, _shot, seed_kind_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _row(page, name):
    return page.locator("tr:has([data-field='element-name']):has-text('%s')" % name).filter(
        has=page.locator("[data-field='element-part-of']")).first


def test_the_application_layer_names_what_each_row_is_part_of(page, live_server):
    org = seed_kind_org()
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, org["emails"][ARCHITECT])
    page.goto(live_server + "/architecture/dashboard?layer=application", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    part = page.locator("tr", has=page.get_by_role("button", name="Open details for Alerts Gateway", exact=True))
    expect(part.locator("[data-field='element-part-of']")).to_have_text("Alerts gateway and consumers",
                                                                       timeout=PAGE_TIMEOUT)
    whole = page.locator("tr", has=page.get_by_role("button", name="Open details for Alerts gateway and consumers"))
    expect(whole.locator("[data-field='element-part-of']")).to_have_text("Not part of an application")
    _shot(page, "1440-light-application-layer")

    part.locator("[data-field='element-part-of'] a").click()
    expect(page.get_by_test_id("application-parts")).to_contain_text("Alerts Gateway", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("application-parts")).to_contain_text("Alerts Consumers")
    assert page.url.endswith("/applications/%d" % org["ids"]["Alerts gateway and consumers"])
