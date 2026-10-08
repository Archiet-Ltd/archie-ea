"""L7, "what data does <element> hold or produce, who stewards it, and where does it flow?":
the Data question on the Ask page, in a real browser.

A seeded element has one data object (steward as recorded text, no owner) and one lineage
edge out to another element. The answer shows the object with "Not recorded" where a value is
missing, the flow by the other end's name, and never the sensitive fields. It shares one
picker with the other questions, so the impact question must keep its own answer separate.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_data_graph(org_id):
    from app import create_app, db
    from app.models.all_missing_models import DataLineage
    from app.models.application_layer import DataObject
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    noun = "Ledgerd %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        service = ArchiMateElement(
            name="%s Service" % noun, type="ApplicationComponent", layer="application", organization_id=org_id,
        )
        target = ArchiMateElement(
            name="%s Warehouse" % noun, type="ApplicationComponent", layer="application", organization_id=org_id,
        )
        db.session.add_all([service, target])
        db.session.commit()
        db.session.add(DataObject(
            name="%s Orders" % noun, archimate_element_id=service.id, organization_id=org_id,
            data_classification="Confidential", data_steward="Riley Steward",
            pii_fields='["secret_field"]', storage_location="s3://secret-bucket",
        ))
        db.session.add(DataLineage(
            name="edge", archimate_element_id=service.id, target_archimate_element_id=target.id,
            lineage_type="ETL", frequency="Daily", organization_id=org_id,
        ))
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=service.id, target_id=target.id, organization_id=org_id,
        ))
        db.session.commit()
    return {"noun": noun}


@pytest.fixture(scope="module")
def data_graph(seeded, live_server):
    return _seed_data_graph(seeded["ids"]["org"])


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _ready(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _pick(page, noun, option_text):
    type_and_wait(page, "ask", noun)
    page.locator("#ask-picker-listbox [role=option]", has_text=option_text).click()


def test_the_data_question_shows_objects_steward_and_flows_but_not_sensitive_fields(
    page, live_server, seeded, data_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    card = page.locator("#ask-question-data")
    expect(card).to_be_visible()
    expect(page.get_by_text("What data does")).to_be_visible()
    expect(page.get_by_text("hold or produce, who stewards it, and where does it flow?")).to_be_visible()

    card.click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    _pick(page, data_graph["noun"], "Service")

    row = page.locator("[data-ask-data-row]")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Orders")
    expect(row).to_contain_text("Riley Steward")
    expect(row).to_contain_text("Owner: Not recorded")
    flow = page.locator("[data-ask-data-flow]")
    expect(flow).to_have_count(1)
    expect(flow).to_contain_text("Flows to")
    expect(flow).to_contain_text("Warehouse")

    body = page.locator("#ask-data-results").inner_text()
    assert "secret_field" not in body and "secret-bucket" not in body


def test_an_element_with_no_data_object_says_so_and_the_questions_stay_separate(
    page, live_server, seeded, data_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-data").click()
    _pick(page, data_graph["noun"], "Warehouse")
    # The warehouse holds no data object of its own and is the target of the seeded edge.
    expect(page.get_by_text("No data objects are recorded for this element.")).to_be_visible()
    assert page.locator("[data-ask-data-row]").count() == 0
    expect(page.locator("[data-ask-data-flow]")).to_contain_text("Comes from")

    page.locator("#ask-question-impact").click()
    _pick(page, data_graph["noun"], "Service")
    page.wait_for_selector("[data-ask-row]")
    expect(page.locator("#ask-results")).to_be_visible()
    expect(page.locator("#ask-data-results")).to_be_hidden()
