"""platform_admin: add a webhook subscription, send a test event and replay logged events.

The journey drives /admin/webhook-settings in a real browser against the real
database. The subscription points at a public https address; the request itself
may fail in a sandbox, which is fine because what the journey checks is the
delivery row the screen shows: the test event with its signature status, and the
replayed events marked as replays after a reload.
"""

import re
import socket

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TARGET = "https://example.com/entelim-webhook-journey"


def _require_public_dns():
    try:
        socket.getaddrinfo("example.com", 443)
    except OSError:
        pytest.skip(
            "no DNS here: a subscription URL must resolve to a public address to be accepted"
        )


def _emit_into_log(app, org_id, count):
    """Put *count* events in the organisation's log; returns the first new sequence number."""
    from app.services.event_log_service import max_ordinal
    from tests._webhook_helpers import emit_events

    with app.app_context():
        first = max_ordinal(org_id) + 1
        emit_events(org_id, count)
    return first


def test_platform_admin_adds_a_subscription_sends_a_test_and_replays(
    browser, live_server, seeded, app
):
    _require_public_dns()
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/webhook-settings", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Add Webhook Subscription")).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        page.fill("#url", TARGET)
        page.fill("#description", "journey subscription")
        page.get_by_role("button", name="Save webhook subscription").click()

        # The signing secret is shown once, on the response to the save.
        expect(page.get_by_text("Copy your signing secret now")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("#new-secret-value")).to_have_value(re.compile(r"^[0-9a-f]{64}$"))
        page.goto(live_server + "/admin/webhook-settings", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Copy your signing secret now")).to_have_count(0)
        expect(page.get_by_text(TARGET).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # Send a test event, reload, see the delivery and its signature status.
        page.get_by_role("button", name="Send a test event to " + TARGET).first.click()
        panel = page.locator("#deliveries-panel")
        expect(panel).to_contain_text("webhook.test", timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        expect(panel).to_contain_text("webhook.test", timeout=PAGE_TIMEOUT)
        expect(panel).to_contain_text("Signed at", timeout=PAGE_TIMEOUT)
        expect(panel.locator("tr", has_text="Test")).not_to_have_count(0)

        # Replay from an earlier sequence, reload, see the replays marked.
        first = _emit_into_log(app, seeded["ids"]["org"], 3)
        page.fill("#replay-sequence", str(first))
        page.get_by_role("button", name="Replay", exact=True).first.click()
        # The count can exceed three: the organisation's log also holds the events its own
        # seeding produced after this point, and they are replayed in the same order.
        expect(page.get_by_text(re.compile(r"Queued \d+ event\(s\) for replay"))).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        page.reload(timeout=PAGE_TIMEOUT)
        replayed = page.locator("#deliveries-panel tr", has_text="Replay")
        expect(replayed.nth(2)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(replayed.first).to_contain_text("archimate_element.")
        expect(replayed.first).to_contain_text("pending")
    finally:
        page.close()
