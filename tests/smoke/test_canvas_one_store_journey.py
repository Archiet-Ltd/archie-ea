"""A business architect whose canvas holds a Stakeholder in Customer Segments sees the same entries, in the
same order, on the canvas page, in the JSON the chat's get_canvas reads, and in the chat's answer to
"who are our customer segments?". Text kept from before the canvas listed elements is shown apart, never as
a segment. Every link on the chat card opens a page that has content.

Runs at 1440 wide in the light theme and at 390 wide in the dark theme.
"""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("CANVAS_STORE_SHOTS", "") or "/tmp/canvas-store-shots")
SEGMENTS = ["Regional logistics firms", "Scale-up CTO / VP Engineering"]
QUESTION = "who are our customer segments?"


def seed_canvas(org_id, canvas_name="Archiet model", first="Regional logistics firms", legacy="Agencies"):
    """A Business Model Canvas whose Customer Segments hold a Business Actor and a Stakeholder with two
    relationships, a partner in Key Partners, and legacy text in the old segments column."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service as ms

    app = create_app("testing")
    with app.app_context():
        def make(name, el_type, layer):
            el = ArchiMateElement(name=name, type=el_type, layer=layer, organization_id=org_id)
            db.session.add(el)
            db.session.flush()
            return el

        actor = make(first, "BusinessActor", "business")
        stakeholder = make("Scale-up CTO / VP Engineering", "Stakeholder", "motivation")
        host = make("Cloud host", "BusinessActor", "business")
        driver = make("Faster releases", "Driver", "motivation")
        goal = make("Cut hosting cost", "Goal", "motivation")
        for other in (driver, goal):
            db.session.add(ArchiMateRelationship(
                type="association", source_id=stakeholder.id, target_id=other.id, organization_id=org_id))
        canvas = BusinessModelCanvas(name=canvas_name, organization_id=org_id)
        db.session.add(canvas)
        db.session.flush()
        ms.add_entry(canvas, "customer_segments", element_id=actor.id)
        ms.add_entry(canvas, "customer_segments", element_id=stakeholder.id)
        ms.add_entry(canvas, "key_partners", element_id=host.id)
        canvas.customer_segments = legacy
        db.session.commit()
        return {"canvas": canvas.id, "stakeholder": stakeholder.id}


def shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=False)


def page_segments(page):
    notes = page.locator('[data-canvas-notes="customer-segments"] > li a[data-note-text]')
    return [t.strip() for t in notes.all_inner_texts()]


def chat_names(card, canvas_name):
    """The element records the card lists for customer segments on that canvas, in order. The shared
    organisation may hold canvases from other journeys, so the canvas is named."""
    names = []
    for record in card.locator("[data-catalogue-record]").all():
        source = record.locator("[data-catalogue-source]").inner_text()
        if source.startswith("Customer Segments on %s · " % canvas_name):
            names.append(record.get_by_test_id("catalogue-open").inner_text().strip())
    return names


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_page_api_and_chat_list_the_same_customer_segments(browser, live_server, seeded, width, scheme):
    canvas_name = "Archiet model" if width == 1440 else "Archiet model on a phone"
    ids = seed_canvas(seeded["ids"]["org"], canvas_name=canvas_name)
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in_as(page, live_server, seeded, "business_architect")

        # The page lists both segments, the person among them, and each opens a page headed by its name.
        page.goto("%s/business-model/%d" % (live_server, ids["canvas"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page_segments(page) == SEGMENTS
        shot(page, "canvas", width, scheme)
        for name in SEGMENTS:
            page.goto("%s/business-model/%d" % (live_server, ids["canvas"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.locator('[data-canvas-notes="customer-segments"] a[data-note-text]', has_text=name).click()
            page.wait_for_load_state("domcontentloaded")
            expect(page.locator("h1").first).to_contain_text(name)

        # The route get_canvas calls lists the same two names in the same order; "Agencies" is earlier text only.
        data = page.request.get("%s/business-model/api/%d" % (live_server, ids["canvas"])).json()["data"]
        box = next(b for b in data["boxes"] if b["key"] == "customer_segments")
        assert [e["name"] for e in box["entries"]] == SEGMENTS
        assert "Agencies" not in [e["name"] for b in data["boxes"] for e in b["entries"]]
        assert data["earlier_text"]["customer_segments"] == "Agencies"

        # The chat's answer lists the same two names in the same order, on the canvas, without the partner.
        card = ask(page, live_server, QUESTION)
        expect(card.locator("[data-catalogue-cards]")).to_have_attribute("data-catalogue-entry", "canvas_box_entries")
        assert chat_names(card, canvas_name) == SEGMENTS
        text = card.inner_text()
        assert canvas_name in text
        assert "Cloud host" not in text and "Agencies" not in text
        shot(page, "chat", width, scheme)

        # Every link on the card opens a page with content.
        hrefs = card.locator("[data-testid=catalogue-open]").evaluate_all("els => els.map(e => e.href)")
        assert len(hrefs) >= 3
        for href in hrefs:
            response = context.request.get(href)
            assert response.status == 200, href
            assert len(response.text()) > 2000, href
    finally:
        context.close()


COST_QUESTION = "what is in our cost structure?"


def seed_cost_canvas(org_id, canvas_name):
    """A canvas whose organisation has two applications with an annual cost and a technology cost that one of
    them carries, so the cost box holds direct lines and a technology share."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_model import BusinessModelCanvas
    from app.services import cost_fact_store, technology_cost
    from config import CurrencyConfig

    app = create_app("testing")
    with app.app_context():
        currency = CurrencyConfig.get_org_currency_code(organization_id=org_id)
        canvas = BusinessModelCanvas(name=canvas_name, organization_id=org_id)
        db.session.add(canvas)
        db.session.flush()
        apps = []
        for name in ("Billing ledger", "Order portal"):
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            row = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id)
            db.session.add(row)
            db.session.flush()
            apps.append(row)
        for amount, row in zip((48000, 12000), apps):
            cost_fact_store.upsert_fact(org_id, "application", row.id, amount, currency, "journey")
        node = ArchiMateElement(name="Shared cluster", type="Node", layer="technology", organization_id=org_id)
        db.session.add(node)
        db.session.flush()
        db.session.add(ArchiMateRelationship(
            source_id=node.id, target_id=apps[0].archimate_element_id, type="realization", organization_id=org_id))
        db.session.flush()
        technology_cost.record_cost(org_id, node.id, "9000", currency, "hosting")
        db.session.commit()
        return {"canvas": canvas.id}


def test_a_cto_sees_the_cost_lines_and_a_business_architect_is_told_why_not(browser, live_server, seeded):
    canvas_name = "Costed model"
    ids = seed_cost_canvas(seeded["ids"]["org"], canvas_name)
    for persona, sees in (("cto", True), ("business_architect", False)):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        context.set_default_timeout(PAGE_TIMEOUT)
        context.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        try:
            sign_in_as(page, live_server, seeded, persona)
            data = page.request.get("%s/business-model/api/%d" % (live_server, ids["canvas"])).json()["data"]
            costs = next(b for b in data["boxes"] if b["key"] == "cost_structure")
            card = ask(page, live_server, COST_QUESTION)
            expect(card.locator("[data-catalogue-cards]")).to_have_attribute("data-catalogue-entry", "canvas_box_entries")
            text = card.inner_text()
            shot(page, "cost-chat-" + persona, 1440, "light")
            if sees:
                texts = [line["text"] for line in costs["costs"]["lines"]]
                assert any(t.startswith("Billing ledger: 48,000") for t in texts)
                assert any(t.startswith("Billing ledger, technology share: 9,000") for t in texts)
                assert any(t.startswith("Order portal: 12,000") for t in texts)
                records = [r for r in card.locator("[data-catalogue-record]").all()
                           if r.locator("[data-catalogue-source]").inner_text().startswith("Cost Structure on %s" % canvas_name)]
                assert len(records) >= 3
                assert any("technology share" in r.locator("[data-catalogue-source]").inner_text() for r in records)
                assert "Billing ledger" in text and "Order portal" in text
                hrefs = card.locator("[data-testid=catalogue-open]").evaluate_all("els => els.map(e => e.href)")
                assert hrefs
                for href in hrefs:
                    response = context.request.get(href)
                    assert response.status == 200, href
                    assert len(response.text()) > 2000, href
            else:
                assert costs["costs"]["lines"] == [] and costs["empty_reason"] == "Cost figures are withheld for your role."
                assert "Cost figures are withheld for your role." in text
                assert "cannot answer this yet" not in text
                assert "48,000" not in text and "48000" not in text and "Billing ledger" not in text
        finally:
            context.close()


WITHHELD = "Cost figures are withheld for your role."


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_the_canvas_page_shows_cost_lines_to_a_cto_and_the_reason_to_a_business_architect(
        browser, live_server, seeded, width, scheme):
    ids = seed_cost_canvas(seeded["ids"]["org"], "Costed page model")
    for persona, sees in (("cto", True), ("business_architect", False)):
        context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme,
                                      ignore_https_errors=True)
        context.set_default_timeout(PAGE_TIMEOUT)
        context.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        try:
            sign_in_as(page, live_server, seeded, persona)
            page.goto("%s/business-model/%d" % (live_server, ids["canvas"]))
            box = page.locator("#box-cost-structure + *")
            expect(box).to_be_visible()
            box.scroll_into_view_if_needed()
            text = box.inner_text()
            shot(page, "cost-page-" + persona, width, scheme)
            if sees:
                assert "Billing ledger: 48,000" in text
                assert "Billing ledger, technology share: 9,000" in text
                assert "Order portal: 12,000" in text
                assert WITHHELD not in text
            else:
                assert WITHHELD in text
                assert box.locator("[data-canvas-withheld]").count() == 1
                assert "48,000" not in text and "12,000" not in text and "9,000" not in text
                assert "Billing ledger" not in text and "No application costs" not in text
                assert "48,000" not in page.content()
            card = ask(page, live_server, COST_QUESTION)
            shot(page, "cost-chat-" + persona, width, scheme)
            chat = card.inner_text()
            assert ("Billing ledger" in chat) is sees
            assert (WITHHELD in chat) is (not sees)
        finally:
            context.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_an_empty_canvas_cost_box_says_why_it_is_empty_to_each_kind_of_person(
        browser, live_server, seeded, width, scheme):
    from app import create_app, db
    from app.models.business_model import BusinessModelCanvas

    org_id = seeded["ids"]["org"]
    with create_app("testing").app_context():
        canvas = BusinessModelCanvas(name="Empty cost page model", organization_id=org_id)
        db.session.add(canvas)
        db.session.commit()
        canvas_id = canvas.id
    for persona, sees in (("cto", True), ("business_architect", False)):
        context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme,
                                      ignore_https_errors=True)
        context.set_default_timeout(PAGE_TIMEOUT)
        context.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        try:
            sign_in_as(page, live_server, seeded, persona)
            page.goto("%s/business-model/%d" % (live_server, canvas_id))
            box = page.locator("#box-cost-structure + *")
            expect(box).to_be_visible()
            box.scroll_into_view_if_needed()
            reason = box.locator('[data-canvas-empty="cost-structure"]')
            expect(reason).to_be_visible()
            text = " ".join(reason.inner_text().split())
            shot(page, "empty-cost-page-" + persona, width, scheme)
            if sees:
                assert WITHHELD not in text
                assert "cost" in text.lower() and len(text.split()) >= 8
            else:
                assert WITHHELD in text
                assert not any(ch.isdigit() for ch in text)
        finally:
            context.close()
