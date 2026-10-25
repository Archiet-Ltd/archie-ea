"""An enterprise architect defines a custom property and sees how many records use it."""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def linked_records(app, seeded):
    """Two applications in the signed-in organisation, none carrying the new property."""
    from app import db
    from app.models import ArchiMateElement
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        org_id = owner.organization_id
        names = ["Ledger %s" % suffix, "Billing %s" % suffix]
        ids = []
        for name in names:
            element = ArchiMateElement(
                name=name, type="ApplicationComponent", layer="application", organization_id=org_id
            )
            db.session.add(element)
            db.session.flush()
            ids.append(element.id)
        db.session.commit()
        payload = {"org_id": org_id, "ids": ids, "names": names, "suffix": suffix}

    yield payload

    with app.app_context():
        from app.models.acm_property_template import AcmPropertyTemplate

        for element_id in payload["ids"]:
            element = db.session.get(ArchiMateElement, element_id)
            if element is not None:
                db.session.delete(element)
        AcmPropertyTemplate.query.filter(
            AcmPropertyTemplate.organization_id == payload["org_id"],
            AcmPropertyTemplate.display_name.like("%%%s" % payload["suffix"]),
        ).delete(synchronize_session=False)
        db.session.commit()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_architect_defines_a_property_and_sees_how_many_records_use_it(
    app, browser, live_server, seeded, linked_records
):
    from app import db
    from app.models import ArchiMateElement
    from app.models.user import User
    from app.modules.architecture_assistant.property_service import PropertyService

    name = "Recovery time %s" % linked_records["suffix"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        with app.app_context():
            user = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
            assert user.enterprise_role == "enterprise_architect"

        page.goto(live_server + "/metamodel/properties", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The picker offers plain names grouped by layer, with stored keys unchanged.
        picker = page.locator("#mm-type")
        labels = picker.locator("option").all_text_contents()
        assert "Application" in labels and "Business process" in labels
        for raw in ("ApplicationComponent", "BusinessProcess", "TechnologyService"):
            assert raw not in labels
        groups = picker.locator("optgroup").evaluate_all("els => els.map(e => e.label)")
        assert groups[:3] == ["Strategy", "Business", "Applications"]
        assert picker.locator("optgroup[label='Business'] option", has_text="Business process").count() == 1
        assert picker.locator("option[value='BusinessProcess']").count() == 1

        # Define it through the form.
        picker.select_option(value="ApplicationComponent")
        page.fill("#mm-name", name)
        page.select_option("#mm-value-type", "number")
        page.get_by_role("button", name="Add property").click()
        page.wait_for_url(lambda url: "/metamodel/properties/" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Application")
        expect(page.locator("body")).not_to_contain_text("ApplicationComponent")

        # Set it on exactly one of the two records.
        with app.app_context():
            element = db.session.get(ArchiMateElement, linked_records["ids"][0])
            PropertyService().set_element_property(element, "recovery_time_%s" % linked_records["suffix"], 4)
            db.session.commit()

        page.goto(live_server + "/metamodel/properties", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("tr", has_text=name)
        expect(row.locator("td").nth(1)).to_have_text("Application")
        expect(row.locator("[data-testid=usage-count]")).to_have_text("1")
        expect(page.locator("body")).not_to_contain_text("ApplicationComponent")

        # The row links to a page that names the record still missing a value.
        row.get_by_role("link", name=name).click()
        page.wait_for_url(lambda url: "/metamodel/properties/" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(linked_records["names"][1])
        expect(page.locator("body")).not_to_contain_text(linked_records["names"][0])
    finally:
        page.close()


def test_a_new_organisation_sees_what_a_property_is_for_and_a_way_to_define_one(
    app, browser, live_server, seeded
):
    from app.models.acm_property_template import AcmPropertyTemplate
    from app.models.user import User

    with app.app_context():
        org_id = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one().organization_id
        assert AcmPropertyTemplate.query.filter(AcmPropertyTemplate.organization_id == org_id).count() == 0

    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/metamodel/properties", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.locator("div[role=status]", has_text="No custom properties yet")
        expect(empty).to_contain_text("No custom properties yet")
        expect(empty).to_contain_text("extra fact")
        empty.get_by_role("link", name="Define a property").click()
        expect(page.locator("#mm-type")).to_be_visible()
        expect(page.get_by_role("button", name="Add property")).to_be_visible()
    finally:
        page.close()
