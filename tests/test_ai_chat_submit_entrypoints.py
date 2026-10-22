"""Additional public chat-submit paths using the real rendered browser harness.

Entity analysis uses ordinary Query/result/Ask AI clicks. Its one search result
is an explicitly intercepted lookup fixture; streaming still uses the inherited
real loopback HTTP server. The document bridge is a test-dispatched integration
CustomEvent, not an ordinary-click claim or a test of document extraction.
No production function replacement, database or external provider is involved.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.test_ai_model_selector_display import browser, launch  # noqa: F401

pytestmark = pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
MODEL = {'model': 'fixture-0', 'display_name': 'Fixture 0', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None}


def chat(launch):
    page, payloads = launch([MODEL])
    expect(page.locator('#model-selector-empty-note')).to_contain_text('One AI model is configured')
    page.get_by_role('button', name='Toggle advanced settings', exact=True).click()
    return page, payloads


def assert_one_reply(page, payloads, message, initial_url):
    expect(page.locator('#messages-container .message-content').last).to_contain_text('Fixture response.')
    assert payloads == [{'message': message, 'domain': 'general', 'element_id': None,
                         'context_type': None, 'persona': None, 'model': None, 'thread_id': None, 'starter': None}]
    assert page.url == initial_url


def test_entity_analysis_ordinary_click_uses_shared_submit_path(launch):
    page, payloads = chat(launch)
    initial_url = page.url
    lookups = []

    def lookup(route):
        assert route.request.method == 'POST'
        lookups.append(route.request.post_data_json)
        route.fulfill(json={'success': True, 'result_count': 1,
                            'explanation': 'Fixture application lookup', 'suggestions': [],
                            'results': [{'id': 32, 'entity_type': 'Application',
                                         'name': 'Fixture Payments', 'description': 'Lookup fixture'}]})

    # Only this read-only lookup boundary is intercepted; the actual app builds
    # the result and action panel, and the real stream fixture receives the send.
    page.route('**/ai-chat/nl-query', lookup)
    toggle = page.get_by_role('button', name='Toggle conversation panel', exact=True)
    if toggle.get_attribute('aria-expanded') == 'false':
        toggle.click()
    page.get_by_role('button', name='Query', exact=True).click()
    page.get_by_label('Natural language query', exact=True).fill('List applications')
    page.get_by_role('button', name='Execute query', exact=True).click()
    page.locator('#nl-results-list').get_by_text('Fixture Payments', exact=True).click()
    panel = page.locator('#entity-action-modal')
    expect(panel).to_be_visible()
    panel.get_by_role('button', name=re.compile(r'^Ask AI')).click()
    expect(panel).to_have_count(0)
    assert_one_reply(page, payloads,
        'Analyze "Fixture Payments" (Application, ID: 32).\n\nPlease provide:\n'
        '1. Overview and current status\n2. Key relationships and dependencies\n'
        '3. Risk assessment\n4. Improvement recommendations', initial_url)
    assert lookups == [{'query': 'List applications', 'persona': ''}]


def test_document_question_integration_event_submits_question_and_context(launch):
    page, payloads = chat(launch)
    initial_url = page.url
    # This is the documented public document-panel bridge, explicitly injected
    # by the test. It is not a synthetic click or a copied private submit helper.
    page.evaluate("""() => window.dispatchEvent(new CustomEvent('ask-question', {
        detail: {question: 'Document follow-up fixture.', context: 'Fixture document excerpt'}
    }))""")
    assert_one_reply(page, payloads,
                     'Document follow-up fixture.\n\n[Context: Fixture document excerpt]', initial_url)


def test_shift_enter_inserts_newline_without_sending_then_enter_sends_once(launch):
    page, payloads = chat(launch)
    initial_url = page.url
    composer = page.locator('#user-input')
    composer.fill('First fixture line')
    composer.press('Shift+Enter')
    expect(composer).to_have_value('First fixture line\n')
    assert payloads == []
    composer.press_sequentially('Second fixture line')
    expect(composer).to_have_value('First fixture line\nSecond fixture line')
    composer.press('Enter')
    assert_one_reply(page, payloads, 'First fixture line\nSecond fixture line', initial_url)


def test_empty_enter_does_not_send_and_next_nonempty_enter_remains_usable(launch):
    page, payloads = chat(launch)
    initial_url = page.url
    composer = page.locator('#user-input')
    composer.fill('   ')
    composer.press('Enter')
    expect(composer).to_have_value('   ')
    expect(page.locator('#send-btn')).to_be_enabled()
    assert payloads == []
    assert page.url == initial_url
    composer.fill('Nonempty fixture after empty Enter.')
    composer.press('Enter')
    assert_one_reply(page, payloads, 'Nonempty fixture after empty Enter.', initial_url)
