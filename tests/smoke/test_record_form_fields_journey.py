"""A capability's maturity level and a node's location are saved from the Edit page and read back.

An enterprise architect opens a capability, clicks Edit, chooses a maturity level and saves; after a
reload the record page shows it and the Edit form opens on the same level. The same for a node's
location. Nothing the form shows is dropped on the way.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("RECORD_FIELDS_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _make(org_id, name):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.technology_layer import Node

    with create_app("testing").app_context():
        capability = BusinessCapability(organization_id=org_id, name=name + " capability")
        node = Node(organization_id=org_id, name=name + " node")
        db.session.add_all([capability, node])
        db.session.commit()
        ids = capability.id, node.id
        db.session.remove()
    return ids


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_a_maturity_level_and_a_location_are_saved_and_reopen_on_the_form(browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    name = "Fields %s" % uuid.uuid4().hex[:6]
    capability_id, node_id = _make(org["org_id"], name)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)

        for layer, kind, record_id, label, choose, shown in (
            ("strategy", "Capability", capability_id, "Maturity Level", "3", "Current maturity level"),
            ("technology", "Node", node_id, "Location / Data Centre", "Dublin DC2", "Dublin DC2"),
        ):
            record = "/architecture/%s/%s/%d" % (layer, kind, record_id)
            page.goto(live_server + record, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_role("link", name="Edit").first.click()
            form = page.locator("#archimate-form-modal")
            field = form.get_by_label(label)
            expect(field).to_be_visible(timeout=PAGE_TIMEOUT)
            if kind == "Capability":
                field.select_option(choose)
            else:
                field.fill(choose)
            _frame(page, "%s-fields-edit" % kind, viewport, theme)
            form.get_by_role("button", name="Update").click()
            page.wait_for_url(re.compile(r"/%s/%d$" % (kind, record_id)), timeout=PAGE_TIMEOUT)

            page.reload(wait_until="domcontentloaded")
            assert shown in page.locator("body").inner_text()
            page.get_by_role("link", name="Edit").first.click()
            reopened = page.locator("#archimate-form-modal").get_by_label(label)
            expect(reopened).to_have_value(choose, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
