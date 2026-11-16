"""Match entities: Accept saves the entity to the model, Details opens its page.

The person is the enterprise architect. They paste a document that mentions a
Customer Portal, accept it as a new entity, follow the link to its page, then
paste the same document again: it is now a match, Accept creates nothing and
Details opens the same page. The AI answer is stubbed (SMOKE_AI_PROTOCOL_STUB=1).
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ENTITY_MATCHING_WALK_DIR")
NAME = "Customer Portal"
TEXT = "We are adding a new Customer Portal application for billing."
VIEWPORTS = [
    pytest.param({"width": 1440, "height": 900}, "light", "1440-light", id="1440-light"),
    pytest.param({"width": 390, "height": 844}, "dark", "390-dark", id="390-dark"),
]


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _portal_elements(app, org_id, remove=False):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with app.app_context():
        db.session.remove()
        rows = db.session.query(ArchiMateElement).filter_by(organization_id=org_id, name=NAME).all()
        ids = [r.id for r in rows]
        if remove:
            for r in rows:
                db.session.delete(r)
            db.session.commit()
        db.session.remove()
        return ids


def _tab(page, name):
    page.locator(".sidebar-content").get_by_role("button", name=re.compile("^" + name)).click()
    return page.locator(".sidebar-content .entity-card", has_text=NAME).first


def _send(page, live_server):
    page.goto(live_server + "/ai-chat/entity-matching", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_placeholder("Type your message or paste document text here...").fill(TEXT)
    with page.expect_response(
        lambda r: r.url.endswith("/ai-chat/entity-matching") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ):
        page.get_by_role("button", name="Send message").click()


@pytest.mark.parametrize("viewport,scheme,label", VIEWPORTS)
def test_accept_then_details(browser, live_server, seeded, ai_protocol_stub, app, viewport, scheme, label):
    if ai_protocol_stub is None:
        pytest.skip("SMOKE_AI_PROTOCOL_STUB=1 is needed for the stubbed AI answer")
    org_id = seeded["ids"]["org"]
    _portal_elements(app, org_id, remove=True)
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["enterprise_architect"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").dispatch_event("click")
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)

        # New entity: Accept saves it once and the row links to it.
        _send(page, live_server)
        accept = _tab(page, "New").get_by_role("button", name="Accept")
        with page.expect_response(lambda r: r.url.endswith("/entity-matching/accept"), timeout=PAGE_TIMEOUT) as resp:
            accept.click()
        assert resp.value.status == 200, resp.value.text()
        added = _tab(page, "New").get_by_test_id("entity-added")
        added.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Added to your model" in added.inner_text()
        stats = page.locator(".sidebar-content .grid").first.inner_text()
        assert re.search(r"New:\s*1\b", stats), stats
        _shot(page, "accept-done-" + label)
        ids = _portal_elements(app, org_id)
        assert len(ids) == 1

        # The link lands on the element's page, with its name.
        added.get_by_role("link", name=NAME).click()
        page.wait_for_url("**/architecture/traceability/%d" % ids[0], timeout=PAGE_TIMEOUT)
        assert NAME in page.locator("main").inner_text()

        # Same document again: it is a match. Accept creates nothing, Details opens the same page.
        _send(page, live_server)
        _tab(page, "Matches").get_by_role("button", name="Accept").click()
        matched = _tab(page, "Matches").get_by_test_id("entity-added")
        matched.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Already in your model" in matched.inner_text()
        assert _portal_elements(app, org_id) == ids
        _shot(page, "matched-done-" + label)

        _send(page, live_server)
        _tab(page, "Matches").get_by_role("button", name="Details").click()
        page.wait_for_url("**/architecture/traceability/%d" % ids[0], timeout=PAGE_TIMEOUT)
        assert NAME in page.locator("main").inner_text()
        _shot(page, "details-" + label)
        assert _portal_elements(app, org_id) == ids
    finally:
        page.close()
        _portal_elements(app, org_id, remove=True)
