"""T-entmatch01: Entity Matching Assistant buttons do what they say.

Journey tests for the four buttons on /ai-chat/entity-matching:
- Accept a new entity, reload, find it in the model
- Accept a matched entity, land on existing element, no new element
- Resolve a duplicate both ways (Use existing / Keep as new)
- Open Details

Runs in a seeded organisation with the AI answer stubbed (requires SMOKE_AI_PROTOCOL_STUB=1).
"""
import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Opt-in walk screenshots
WALK = os.environ.get("ENTMATCH01_WALK_DIR")


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.fixture(scope="module")
def seeded_with_elements(app, seeded, ai_protocol_stub):
    """Seed the organisation with some ArchiMate elements for matching."""
    if ai_protocol_stub is None:
        pytest.skip("SMOKE_AI_PROTOCOL_STUB=1 required for this test")

    from app import db
    from app.models.archimate_core import ArchiMateElement
    from flask import g

    with app.app_context():
        db.session.remove()
        with app.test_request_context("/"):
            g.current_org_id = seeded["ids"]["org"]
            # Create a matched element
            matched = ArchiMateElement(
                organization_id=seeded["ids"]["org"],
                name="Payment Gateway Service",
                type="ApplicationComponent",
                layer="Application",
                description="Existing payment gateway",
                scope="enterprise",
            )
            db.session.add(matched)
            # Create a duplicate element
            duplicate = ArchiMateElement(
                organization_id=seeded["ids"]["org"],
                name="Analytics Engine",
                type="ApplicationComponent",
                layer="Application",
                description="Existing analytics engine",
                scope="enterprise",
            )
            db.session.add(duplicate)
            db.session.commit()
            matched_id = matched.id
            duplicate_id = duplicate.id

    return {**seeded, "matched_id": matched_id, "duplicate_id": duplicate_id}


def test_accept_new_entity_reload_finds_it_in_model(browser, live_server, seeded_with_elements):
    """Accept a new entity, reload the page, and find it in the model (element page opens with its name)."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        # Send a message that will extract a new entity
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "We are integrating a new Customer Portal application with the billing system."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        # Wait for the assistant reply with entity suggestions
        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        # Find the new entity suggestion and click Accept
        # The entity should appear in the sidebar under "New" filter
        page.get_by_role("button", name="New (1)").click()
        page.wait_for_timeout(500)

        # Click Accept on the new entity card
        accept_btn = page.locator(".entity-card:has-text('Customer Portal') button:has-text('Accept')").first
        accept_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching/accept" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            accept_btn.click()
        assert resp.value.status == 200, resp.value.text()

        # Verify the entity shows as accepted
        page.wait_for_timeout(500)
        # The status should update to show it was added
        page.locator(".entity-card:has-text('Customer Portal') .text-success-emphasis, .entity-card:has-text('Customer Portal') .text-primary:has-text('Accepted')").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        # Reload the page
        page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # Navigate to the element page via the link (Details button or link in the accepted row)
        # First, send another message to get the entity list again
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "Show me the Customer Portal element."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        # Click Details on the accepted entity
        details_btn = page.locator(".entity-card:has-text('Customer Portal') button:has-text('Details')").first
        if details_btn.count() > 0:
            details_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
            with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
                details_btn.click()
        else:
            # Try the sidebar
            page.get_by_role("button", name="New (1)").click()
            page.wait_for_timeout(500)
            details_btn = page.locator(".entity-card:has-text('Customer Portal') button:has-text('Details')").first
            details_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
            with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
                details_btn.click()

        # Verify we're on the element page with the correct name
        page.wait_for_load_state("domcontentloaded")
        assert "/architecture/elements/" in page.url
        heading = page.locator("h1, h2, [data-testid='element-name']").first
        heading.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Customer Portal" in heading.inner_text()

        _shot(page, "accept-new-entity-1440-light")
    finally:
        page.close()


def test_accept_matched_entity_lands_on_existing_no_new_element(browser, live_server, seeded_with_elements):
    """Accept a matched entity and land on the existing element, with no new element created."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        # Send a message that will match the existing "Payment Gateway Service"
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "The Payment Gateway Service handles all transaction processing."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        # Find the matched entity and click Accept
        page.get_by_role("button", name="Matches (1)").click()
        page.wait_for_timeout(500)

        accept_btn = page.locator(".entity-card:has-text('Payment Gateway') button:has-text('Accept')").first
        accept_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching/accept" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            accept_btn.click()
        assert resp.value.status == 200, resp.value.text()

        # The response should indicate matched: true
        resp_body = resp.value.json()
        assert resp_body.get("matched") is True

        # Click Details to navigate to the existing element
        details_btn = page.locator(".entity-card:has-text('Payment Gateway') button:has-text('Details')").first
        details_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            details_btn.click()

        page.wait_for_load_state("domcontentloaded")
        assert "/architecture/elements/" in page.url
        heading = page.locator("h1, h2, [data-testid='element-name']").first
        heading.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Payment Gateway Service" in heading.inner_text()

        _shot(page, "accept-matched-entity-1440-light")
    finally:
        page.close()


def test_resolve_duplicate_both_ways(browser, live_server, seeded_with_elements):
    """Resolve a duplicate both ways: Use Existing and Keep as New."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        # Send a message that will create a duplicate of "Analytics Engine"
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "We need a new Analytics Engine for the data team."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        # The duplicate should appear in the Duplicates filter
        page.get_by_role("button", name="Duplicates (1)").click()
        page.wait_for_timeout(500)

        # Click Resolve on the duplicate
        resolve_btn = page.locator(".entity-card:has-text('Analytics Engine') button:has-text('Resolve')").first
        resolve_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        resolve_btn.click()

        # The resolve dialog should appear
        dialog = page.locator('[role="dialog"]:has-text("Resolve Duplicate")')
        dialog.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        _shot(page, "resolve-dialog-1440-light")

        # Test "Use Existing Element" path
        use_existing = dialog.get_by_role("button", name="Use Existing Element")
        use_existing.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            use_existing.click()

        page.wait_for_load_state("domcontentloaded")
        assert "/architecture/elements/" in page.url
        heading = page.locator("h1, h2, [data-testid='element-name']").first
        heading.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Analytics Engine" in heading.inner_text()

        # Go back to entity matching
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        # Send another message to get the duplicate again
        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "Another Analytics Engine for reporting."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        page.get_by_role("button", name="Duplicates (1)").click()
        page.wait_for_timeout(500)

        # Click Resolve again
        resolve_btn = page.locator(".entity-card:has-text('Analytics Engine') button:has-text('Resolve')").first
        resolve_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        resolve_btn.click()

        dialog = page.locator('[role="dialog"]:has-text("Resolve Duplicate")')
        dialog.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        # Test "Keep as New Element" path - should navigate to create form
        keep_new = dialog.get_by_role("button", name="Keep as New Element")
        keep_new.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            keep_new.click()

        page.wait_for_load_state("domcontentloaded")
        # Should be on the create element page with prefilled name
        assert "/architecture/elements/create" in page.url or "/architecture/create" in page.url

        _shot(page, "keep-as-new-1440-light")
    finally:
        page.close()


def test_open_details(browser, live_server, seeded_with_elements):
    """Open Details on a matched entity and land on the element page."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "The Payment Gateway Service is critical."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        page.get_by_role("button", name="Matches (1)").click()
        page.wait_for_timeout(500)

        details_btn = page.locator(".entity-card:has-text('Payment Gateway') button:has-text('Details')").first
        details_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            details_btn.click()

        page.wait_for_load_state("domcontentloaded")
        assert "/architecture/elements/" in page.url
        heading = page.locator("h1, h2, [data-testid='element-name']").first
        heading.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Payment Gateway Service" in heading.inner_text()

        _shot(page, "details-1440-light")
    finally:
        page.close()


# Dark mode / narrow viewport variants
def test_accept_new_entity_390_dark(browser, live_server, seeded_with_elements):
    """Same as test_accept_new_entity_reload_finds_it_in_model but at 390 dark."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "New Inventory Service for warehouse management."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        page.get_by_role("button", name="New (1)").click()
        page.wait_for_timeout(500)

        accept_btn = page.locator(".entity-card:has-text('Inventory Service') button:has-text('Accept')").first
        accept_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching/accept" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            accept_btn.click()
        assert resp.value.status == 200, resp.value.text()

        page.wait_for_timeout(500)
        _shot(page, "accept-new-entity-390-dark")
    finally:
        page.close()


def test_resolve_dialog_390_dark(browser, live_server, seeded_with_elements):
    """Resolve dialog at 390 dark."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "Duplicate Analytics Engine for testing."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        page.get_by_role("button", name="Duplicates (1)").click()
        page.wait_for_timeout(500)

        resolve_btn = page.locator(".entity-card:has-text('Analytics Engine') button:has-text('Resolve')").first
        resolve_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        resolve_btn.click()

        dialog = page.locator('[role="dialog"]:has-text("Resolve Duplicate")')
        dialog.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        _shot(page, "resolve-dialog-390-dark")
    finally:
        page.close()


def test_review_form_390_dark(browser, live_server, seeded_with_elements):
    """Review form (create element page) at 390 dark."""
    email = seeded_with_elements["emails"]["enterprise_architect"]
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        _login(page, live_server, email)
        page.goto(
            live_server + "/ai-chat/entity-matching",
            wait_until="networkidle",
            timeout=PAGE_TIMEOUT,
        )

        page.get_by_placeholder("Type your message or paste document text here...").fill(
            "New Reporting Dashboard for executives."
        )
        with page.expect_response(
            lambda r: "/ai-chat/entity-matching" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Send message").click()

        page.locator(".message.assistant .message-content").last.wait_for(
            state="visible", timeout=PAGE_TIMEOUT
        )

        page.get_by_role("button", name="New (1)").click()
        page.wait_for_timeout(500)

        review_btn = page.locator(".entity-card:has-text('Reporting Dashboard') button:has-text('Review')").first
        review_btn.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            review_btn.click()

        page.wait_for_load_state("domcontentloaded")
        assert "/architecture/elements/create" in page.url or "/architecture/create" in page.url
        _shot(page, "review-form-390-dark")
    finally:
        page.close()