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
