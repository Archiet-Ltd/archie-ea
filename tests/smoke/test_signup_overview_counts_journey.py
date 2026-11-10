"""A startup CTO signs up, gives GitHub, and the Overview tells the truth about what the read added.

Sign-up says how many items the read added. The Overview, opened minutes later, says the same total,
all of it from the connected source and none of it by hand: the person typed nothing. Then the person
adds one application in the product and the Overview says one by hand and one more in all.
The same session opens the AI settings page from the sign-up step, saves a key, and still has the way
back to sign-up, which lands on the sign-up step they left.
"""

import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (fixtures and helpers)
    TOKEN,
    new_page,
    people,
    provider,
    server,
    shoot,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _wait_for_the_read(page, base):
    """The sign-up line's own count of what the GitHub read added."""
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        page.goto(base + "/account/welcome", wait_until="domcontentloaded")
        line = page.get_by_test_id("signup-status-github").inner_text()
        found = re.search(r"Read 16 repositories; (\d+) items added to your model", line)
        if found:
            return int(found.group(1))
        page.wait_for_timeout(3000)
    raise AssertionError("the read never finished: %s" % page.get_by_test_id("signup-status-github").inner_text())


def _figures(page, base):
    """The three figures of "Records added" on the Overview, as a person reads them."""
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded")
    total = page.locator("[data-testid^='activity-total-']:visible").first
    expect(total).to_be_visible()
    card = total.locator("xpath=ancestor::div[contains(@class,'gap-3')][1]/..")
    numbers = card.locator("span.tabular-nums")
    connected = int(card.get_by_text("From connected sources").locator("xpath=following-sibling::span[1]").inner_text())
    by_hand = int(card.get_by_text("By hand").locator("xpath=following-sibling::span[1]").inner_text())
    return int(numbers.first.inner_text()), connected, by_hand


def test_the_overview_after_a_sign_up_read_agrees_with_sign_up_and_counts_one_added_by_hand(browser, server, people, app):
    base = str(server)
    context, page = new_page(browser)
    try:
        sign_up(page, base, people)
        page.get_by_label("GitHub organisation").fill("acme")
        page.get_by_label("Read-only token").fill(TOKEN)
        page.get_by_role("button", name="Connect GitHub").click()
        expect(page.get_by_test_id("signup-status-github")).to_contain_text(re.compile("GitHub is connected|Reading GitHub now|Read 16"), timeout=60000)
        added = _wait_for_the_read(page, base)
        assert added == 21, added

        # Step 4: the way to the AI settings page and back, kept after a key is saved.
        page.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded")
        page.get_by_test_id("signup-api-settings-link").click()
        page.wait_for_url("**/admin/api-settings?from=signup")
        expect(page.get_by_test_id("ai-settings-back-to-signup")).to_be_visible()
        page.locator("#api_key").fill("sk-journey-own-key-0002")
        page.locator("#submit").click()
        expect(page.locator("#main-content").get_by_text("saved successfully")).to_be_visible()
        assert "from=signup" in page.url, page.url
        back = page.get_by_test_id("ai-settings-back-to-signup")
        expect(back).to_be_visible()
        shoot(page, "ai-settings-after-save")
        back.click()
        page.wait_for_url("**/account/welcome?step=4")
        expect(page.get_by_role("heading", name="Your AI provider")).to_be_visible()

        # The Overview says what sign-up said, and none of it by hand.
        total, connected, by_hand = _figures(page, base)
        assert (total, connected, by_hand) == (added, added, 0), (total, connected, by_hand)
        shoot(page, "overview-after-a-read")

        # One application typed in by the person is the one by hand.
        page.goto(base + "/applications/", wait_until="domcontentloaded")
        page.get_by_test_id("btn-add-application").click()
        page.locator("#ca-name").fill("Treasury Desk")
        page.locator("form:has(#ca-name) button[type=submit]").click()
        page.wait_for_timeout(2000)
        page.goto(base + "/applications/", wait_until="domcontentloaded")
        expect(page.locator("main")).to_contain_text("Treasury Desk")
        total, connected, by_hand = _figures(page, base)
        assert (total, connected, by_hand) == (added + 1, added, 1), (total, connected, by_hand)
        shoot(page, "overview-after-one-by-hand")
    finally:
        context.close()


def test_a_read_that_cannot_open_the_saved_token_says_so_in_a_plain_sentence(browser, server, people, app):
    """The server's key is not the one the organisation's key was saved with: the review page says what
    failed and who can fix it, in words, never the library's error class."""
    from cryptography.fernet import Fernet

    base = str(server)
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, base, people)
        page.get_by_label("GitHub organisation").fill("acme")
        page.get_by_label("Read-only token").fill(TOKEN)
        page.get_by_role("button", name="Connect GitHub").click()
        expect(page.get_by_test_id("signup-status-github")).to_contain_text(re.compile("GitHub is connected|Reading GitHub now|Read 16"), timeout=60000)
        _wait_for_the_read(page, base)

        # The organisation's key is now one this server's master key did not seal, and the person asks for a read again.
        from app.models.connector_config import ConnectorConfig, OrganizationEncryptionKey
        from app.models.user import User
        from app.services.job_queue_service import get_job_queue_service
        from app.connectors import github as gh

        with app.app_context():
            org_id = User.query.filter_by(email=email).one().organization_id
            key = OrganizationEncryptionKey.query.filter_by(organization_id=org_id).one()
            key.encrypted_key = Fernet(Fernet.generate_key()).encrypt(Fernet.generate_key())
            cfg = ConnectorConfig.query.filter_by(organization_id=org_id, connector_type="github").one()
            get_job_queue_service().create_job(
                "GitHub: read repositories", gh.FIRST_SYNC_TASK, {"connector_id": cfg.id}, organization_id=org_id
            )
            from app import db

            db.session.commit()

        deadline = time.monotonic() + 240
        review = page.locator("main")
        while time.monotonic() < deadline:
            page.goto(base + "/admin/connectors/github/review", wait_until="domcontentloaded")
            if "could not finish" in review.inner_text():
                break
            page.wait_for_timeout(3000)
        text = review.inner_text()
        assert "could not finish" in text, text
        assert "encryption key" in text and "runs this installation" in text, text
        assert "InvalidToken" not in text and "CredentialUnreadable" not in text, text
        shoot(page, "wrong-key-sentence")
        page.get_by_role("link", name="Read again").click()
        page.wait_for_url("**/admin/connectors/github")
        assert len(page.locator("main").inner_text().strip()) > 40
    finally:
        context.close()
