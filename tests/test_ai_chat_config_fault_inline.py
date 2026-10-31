"""A provider-configuration fault in the chat is one inline message naming the remedy, with the link to
the provider settings for an administrator, and no toast and no Retry (a retry could only reproduce it).

The rendered page runs against the real chat scripts in a browser; the stream answers with the no-provider
gate's own 503 body, as the application does when no provider is configured.
"""
import pytest
from playwright.sync_api import expect

from tests.chat_opening_fixture import close_chat_options
from tests.test_ai_model_selector_display import browser, launch  # noqa: F401

pytestmark = pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
MODEL = {'model': 'fixture-0', 'display_name': 'Fixture 0', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None}
ADMIN_SENTENCE = "The assistant is not set up yet. Add an AI provider in Settings to start getting answers."
MEMBER_SENTENCE = "The assistant is not set up yet. Ask an administrator of your organisation to add an AI provider."


def assert_plain(text):
    for word in ("LLM", "feature", "'chat'"):
        assert word not in text, text


def test_a_configuration_fault_is_one_inline_message_with_the_settings_link_and_no_retry(launch):
    page, payloads = launch([MODEL], stream_fault=True)
    expect(page.locator('#model-selector-empty-note')).to_contain_text('One AI model is configured')
    close_chat_options(page)
    page.evaluate("window.chatIsAdmin = true")  # an administrator, who may open the provider settings
    page.fill('#user-input', 'What applications do we own?')
    page.locator('#user-input').press('Enter')

    alerts = page.locator('#messages-container [role="alert"]')
    expect(alerts).to_have_count(1)
    alert = alerts.first
    expect(alert).to_contain_text(ADMIN_SENTENCE)
    expect(alert.locator('[data-testid="chat-provider-settings-link"]')).to_have_attribute('href', '/admin/api-settings')
    text = alert.inner_text()
    assert alert.locator('.js-retry').count() == 0, text
    assert 'try again' not in text.lower(), text
    assert_plain(text)
    assert page.locator('#platform-toast-container [role="alert"]').count() == 0
    assert page.locator('#messages-container .message-content').count() == 1  # the question, and no answer
    assert [p['message'] for p in payloads] == ['What applications do we own?']  # one attempt, no second round trip


def test_a_member_is_told_who_can_fix_a_configuration_fault_and_gets_no_link_retry_or_toast(launch):
    page, payloads = launch([MODEL], stream_fault=True)
    expect(page.locator('#model-selector-empty-note')).to_contain_text('One AI model is configured')
    close_chat_options(page)
    page.evaluate("window.chatIsAdmin = false")
    page.fill('#user-input', 'What applications do we own?')
    page.locator('#user-input').press('Enter')

    alerts = page.locator('#messages-container [role="alert"]')
    expect(alerts).to_have_count(1)
    alert = alerts.first
    expect(alert).to_contain_text(MEMBER_SENTENCE)
    text = alert.inner_text()
    assert alert.locator('[data-testid="chat-provider-settings-link"]').count() == 0, text
    assert alert.locator('a').count() == 0, text
    assert alert.locator('.js-retry').count() == 0, text
    assert 'try again' not in text.lower(), text
    assert_plain(text)
    assert page.locator('#platform-toast-container [role="alert"]').count() == 0
    assert page.locator('#messages-container .message-content').count() == 1
    assert [p['message'] for p in payloads] == ['What applications do we own?']
