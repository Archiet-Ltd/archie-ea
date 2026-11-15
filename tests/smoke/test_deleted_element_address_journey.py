"""An architect deletes an element from its own page. Once they confirm, the element is gone before the page moves
on, and the address they were on now answers "not found" with a plain page, at both widths."""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .test_element_page_edit_and_delete_journey import VIEWPORTS, _held, _make_record, _signed_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _other_element_numbered(org, number):
    """An element of another kind, in the same organisation, whose number is the given one."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        if db.session.execute(db.text("select 1 from archimate_elements where id = :i"), {"i": number}).first():
            return False
        last = db.session.execute(db.text("select last_value from archimate_elements_id_seq")).scalar()
        if number > last:
            return False
        db.session.add(ArchiMateElement(id=number, organization_id=org["org_id"], name="Neighbour %d" % number,
                                        type="Stakeholder", layer="Motivation"))
        db.session.commit()
        return True


def _remove_element(number):
    from app import create_app, db

    with create_app("testing").app_context():
        db.session.execute(db.text("delete from archimate_elements where id = :i"), {"i": number})
        db.session.commit()


def _record_whose_number_a_neighbour_can_take(org, name):
    for _ in range(40):
        ids = _make_record(org, "Goal", name)
        if _other_element_numbered(org, ids["record"]):
            return ids
    pytest.skip("no free element number matched a record number")


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_the_address_of_a_deleted_element_answers_not_found_straight_away(browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    name = "Goal removed %s" % uuid.uuid4().hex[:6]
    ids = _record_whose_number_a_neighbour_can_take(org, name)
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        address = live_server + "/architecture/motivation/Goal/%d" % ids["element"]
        page.goto(address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        page.get_by_test_id("delete-element").click()
        dialog = page.get_by_role("dialog", name="Confirm")
        expect(dialog).to_contain_text(name, timeout=PAGE_TIMEOUT)
        dialog.get_by_role("button", name="Confirm").click()
        page.wait_for_url(re.compile(r"/architecture/motivation/Goal$"), timeout=PAGE_TIMEOUT)
        assert _held(org, "Goal", ids) == (None, None)
        for old in (address, live_server + "/architecture/motivation/Goal/%d" % ids["record"]):
            response = page.goto(old, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 404, "%s answered %s after the delete" % (old, response.status)
            body = page.locator("body").inner_text()
            assert body.strip() and not body.lstrip().startswith("{"), "not a plain page: %r" % body[:120]
            assert name not in body and "Neighbour" not in body
    finally:
        _remove_element(ids["record"])
        context.close()
