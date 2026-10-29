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
    expect(alert).to_contain_text("LLM provider must be configured")
    expect(alert.locator('[data-testid="chat-provider-settings-link"]')).to_have_attribute('href', '/admin/api-settings')
    text = alert.inner_text()
    assert alert.locator('.js-retry').count() == 0, text
    assert 'try again' not in text.lower(), text
    assert page.locator('#platform-toast-container [role="alert"]').count() == 0
    assert page.locator('#messages-container .message-content').count() == 1  # the question, and no answer
    assert [p['message'] for p in payloads] == ['What applications do we own?']  # one attempt, no second round trip
