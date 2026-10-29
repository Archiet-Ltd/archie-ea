"""A solution architect reviews what the Entity Matching page found and the
decision is saved where another page can see it (T-match-01).

Before this, Accept only flipped a client-side status, Review and Resolve did
nothing at all, and Import called the same no-op Accept did -- none of the
four controls reached the server. This drives the real rendered page as a
real persona, clicks each control, and checks the record and its history
line, not just that the button exists.

The suggestions a real chat turn would have produced are set directly on the
page's own Alpine state (the same way test_arb_submission_journey.py and the
composer journeys already do) rather than waiting on a configured LLM
provider: the extraction step is covered elsewhere (test_ai_chat_journey.py)
and by the page's own "no AI model" notice; what this journey is responsible
for is what happens once a person has a suggestion in front of them and
clicks one of the four controls.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_element(org_id, name, description=None):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(
            name=name, type="ApplicationComponent", layer="application",
            description=description, organization_id=org_id,
        )
        db.session.add(element)
        db.session.commit()
        return element.id


def _set_suggestions(page, entities):
    """Put suggestions on the page's own Alpine state, as a real chat turn
    would have -- the four controls under test read only
    ``allEntities``/``entity.data``, never the chat message itself."""
    page.evaluate(
        """(entities) => {
            const root = document.querySelector('.entity-chat-container');
            Alpine.$data(root).allEntities = entities;
        }""",
        entities,
    )


def _history_changes(page):
    to_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    status, body = api(page, "GET", "/intelligence/api/history/changes?from=2000-01-01&to=%s" % to_date)
    assert status == 200, body
    return body["data"]["changes"]


def test_accept_resolve_and_import_save_what_a_person_decides(browser, live_server):
    org = create_fresh_org("solution_architect")
    other = create_fresh_org("solution_architect")
    suffix = uuid.uuid4().hex[:8]

    match_name = "Billing Service %s" % suffix
    match_id = _seed_element(org["org_id"], match_name, description=None)

    duplicate_name = "Claims Portal %s" % suffix
    duplicate_id = _seed_element(org["org_id"], duplicate_name, description="Already described.")

    # Same name as the match above, in the other organisation -- proves a
    # decision on "my" Billing Service never reaches theirs.
    other_id = _seed_element(other["org_id"], match_name, description=None)

    import_name = "Partner Directory %s" % suffix

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["solution_architect"])
    page.goto(live_server + "/ai-chat/entity-matching", wait_until="domcontentloaded")
    page.wait_for_selector(".entity-chat-container")

    _set_suggestions(page, [
        {
            "id": "match_%d" % match_id, "name": match_name, "type": "applicationcomponent",
            "category": "archimate_elements", "status": "matched", "confidence": 92,
            "confidenceLevel": "high", "description": "Existing entity match",
            "data": {
                "entity_id": match_id, "entity_name": match_name,
                "existing_data": {"description": "Extracted from the uploaded document."},
            },
        },
        {
            "id": "dup_%d" % duplicate_id, "name": duplicate_name, "type": "applicationcomponent",
            "category": "archimate_elements", "status": "duplicate", "confidence": 70,
            "confidenceLevel": "medium", "description": "Possible duplicate",
            "data": {"entity_id": duplicate_id, "entity_name": duplicate_name},
        },
        {
            "id": "new_%s" % import_name, "name": import_name, "type": "applicationcomponent",
            "category": "archimate_elements", "status": "new", "confidence": 0,
            "confidenceLevel": "low", "description": "New entity detected",
            "data": {
                "name": import_name,
                "extracted_data": {
                    "type": "ApplicationComponent", "layer": "Application",
                    "description": "A new partner directory service.",
                },
            },
        },
    ])

    toasts = page.locator("#platform-toast-container")

    # --- Accept: the record itself gains what the document had and this
    # organisation's record was missing. ---
    match_card = page.locator(".entity-card").filter(has_text=match_name)
    match_card.get_by_role("button", name="Accept matched entity").click()
    expect(toasts.get_by_text("was filled in from the document", exact=False)).to_be_visible()
    expect(match_card.get_by_text("Saved", exact=False)).to_be_visible()

    # --- Resolve: confirmed as the one record that already existed -- no
    # second row is ever created for a "maybe duplicate". ---
    dup_card = page.locator(".entity-card").filter(has_text=duplicate_name)
    dup_card.get_by_role("button", name="Resolve duplicate entity").click()
    expect(toasts.get_by_text("Confirmed", exact=False)).to_be_visible()
    expect(dup_card.get_by_text("Resolved", exact=False)).to_be_visible()

    # --- Import: a real record a person can find on its own list page. ---
    new_card = page.locator(".entity-card").filter(has_text=import_name)
    new_card.get_by_role("button", name="Import new entity").click()
    expect(toasts.get_by_text("Imported", exact=False)).to_be_visible()
    expect(new_card.get_by_text("Imported", exact=False)).to_be_visible()

    # Accept changed the record, seen on the elements list page (not just
    # the chat's own memory of what it did).
    page.goto(
        live_server + "/architecture/elements?q=%s" % match_name.replace(" ", "+"),
        wait_until="domcontentloaded",
    )
    row = page.locator('[data-testid="element-row"]').filter(has_text=match_name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Extracted from the uploaded document")

    # Resolve kept exactly one record -- it never doubled into two.
    status, body = api(page, "GET", "/archimate/api/elements/search?q=%s" % duplicate_name)
    assert status == 200, body
    dup_rows = [r for r in body["data"] if r["name"] == duplicate_name]
    assert len(dup_rows) == 1, dup_rows

    # Import created the real record, independently findable on its list page.
    page.goto(
        live_server + "/architecture/elements?q=%s" % import_name.replace(" ", "+"),
        wait_until="domcontentloaded",
    )
    imported_row = page.locator('[data-testid="element-row"]').filter(has_text=import_name)
    expect(imported_row).to_have_count(1)
    expect(imported_row).to_contain_text("A new partner directory service")

    # Both decisions left a history line behind, not just a changed value
    # with nothing saying why -- every ArchiMateElement write is already
    # audited generically (app/middleware/audit_middleware.py), so the lines
    # this decision wrote are the ones that carry a reason.
    changes = _history_changes(page)
    decisions = {
        c["record_id"]: c for c in changes
        if c["table_name"] == "archimate_elements" and c.get("change_reason")
    }
    assert match_id in decisions, changes
    assert decisions[match_id]["action"] == "update"
    assert decisions[match_id]["change_reason"] == "confirmed as a match"
    assert duplicate_id in decisions, changes
    assert decisions[duplicate_id]["change_reason"] == "resolved as the same element, not a duplicate"

    # The second organisation's record of the same name is untouched: no
    # description appeared, and it still stands alone.
    context.close()

    other_context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844},
                                         color_scheme="dark")
    other_page = other_context.new_page()
    sign_in(other_page, live_server, other["emails"]["solution_architect"])
    status, body = api(other_page, "GET", "/archimate/api/elements/search?q=%s" % match_name)
    assert status == 200, body
    other_rows = [r for r in body["data"] if r["name"] == match_name]
    assert len(other_rows) == 1, other_rows
    assert other_rows[0]["id"] == other_id
    assert not other_rows[0].get("description"), other_rows

    # Nor does the imported element, or the first organisation's history, leak across.
    status, body = api(other_page, "GET", "/archimate/api/elements/search?q=%s" % import_name)
    assert status == 200, body
    assert body["data"] == [], body
    changes = _history_changes(other_page)
    assert match_id not in {c["record_id"] for c in changes if c["table_name"] == "archimate_elements"}, changes

    other_context.close()


def test_entity_matching_page_says_plainly_when_no_ai_model_is_set_up(browser, live_server):
    org = create_fresh_org("solution_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["solution_architect"])
    page.goto(live_server + "/ai-chat/entity-matching", wait_until="domcontentloaded")
    page.wait_for_selector(".entity-chat-container")

    notice = page.get_by_test_id("entity-matching-model-unavailable-notice")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("No AI model")
    context.close()
