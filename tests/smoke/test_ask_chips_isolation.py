"""The risk and accountability answers on the Ask page stay inside the signed-in organisation.

Two real organisations each hold an element with the same name; each person sees only their
own organisation's risk, and cannot read the other's element through the answer interfaces.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, type_and_wait
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, name, risk_title):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
        db.session.add(element)
        db.session.commit()
        db.session.add(Risk(organization_id=org_id, archimate_element_id=element.id, title=risk_title,
                            likelihood=3, impact=3, owner="team", mitigation_plan="plan"))
        db.session.commit()
        return element.id


def _ask(page, base, name, chip):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function("() => !!document.querySelector('[x-data=\"askSurface()\"]')._x_dataStack")
    page.locator("#ask-question-%s" % chip).click()
    type_and_wait(page, "ask", name)
    expect(page.locator("#ask-picker-listbox [role=option]")).to_have_count(1)
    page.locator("#ask-picker-listbox [role=option]").click()


def test_risk_and_accountability_answers_stay_inside_the_organisation(browser, live_server):
    first = create_fresh_org("solution_architect")
    second = create_fresh_org("solution_architect")
    name = "Shared Ledger %s" % uuid.uuid4().hex[:6]
    first_id = _seed(first["org_id"], name, "First organisation vendor risk")
    second_id = _seed(second["org_id"], name, "Second organisation vendor risk")

    mine = {"first": (first, first_id, second_id), "second": (second, second_id, first_id)}
    own_title = {"first": "First organisation vendor risk", "second": "Second organisation vendor risk"}
    other_title = {"first": "Second organisation", "second": "First organisation"}
    for label, (org, own_id, foreign_id) in mine.items():
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _ask(page, live_server, name, "risk")
        row = page.locator("[data-ask-risk-row]")
        expect(row).to_have_count(1)
        expect(row).to_contain_text(own_title[label])
        assert other_title[label] not in page.locator("#ask-risk-results").inner_text()

        _ask(page, live_server, name, "accountability")
        expect(page.locator("#ask-accountability-results")).to_be_visible()
        assert page.locator("[data-ask-accountability-row]").count() == 0

        for kind in ("risk", "accountability"):
            status, body = api(page, "GET", "/api/v1/intelligence/%s/%d" % (kind, foreign_id))
            text = str(body)
            assert other_title[label] not in text
        context.close()
