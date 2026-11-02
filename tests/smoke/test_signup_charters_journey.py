"""A new owner signs up and opens the AI chat: the organisation they were given already
holds its default agent charters, and neither step leaves a database error in the server log.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat
from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_a_new_owner_signs_up_and_opens_the_chat_with_their_charters_in_place(page, live_server, app):
    email = "charters.%s@example.com" % uuid.uuid4().hex[:8]
    page.context.clear_cookies()
    page.goto(live_server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Casey")
    page.fill("#last_name", "Founder")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.click("#submit")
    page.wait_for_url(lambda url: "/account/register" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/unconfirmed" not in page.url, "the sign-up did not sign the owner in"

    open_chat(page, live_server)
    expect(page.locator("#user-input")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Internal Server Error")

    from app.models.agent_charter import AgentCharter
    from app.models.user import User
    from app.modules.ai_chat.services.architect_persona_charters import ARCHITECT_PERSONAS

    with app.app_context():
        org_id = User.query.filter_by(email=email).one().organization_id
        held = {c.persona for c in AgentCharter.query.filter_by(organization_id=org_id, version=1)}
    assert held == set(ARCHITECT_PERSONAS)

    log = live_server.tail(2000)
    for refusal in ("row-level security", "InsufficientPrivilege", "Traceback"):
        assert refusal not in log, "server log shows %r after sign-up:\n%s" % (refusal, log[-1500:])
