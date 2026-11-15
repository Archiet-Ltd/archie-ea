"""Opt-in enabled AI transport qualification; no external inference or UI doubles.

Run only with SMOKE_AI_PROTOCOL_STUB=1 and a disposable explicit test database.
Replies prove protocol/persistence behavior, not model reasoning or tool quality.
"""
import json
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from .ai_protocol_stub import CHAT, GUIDE, MODEL
from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _format_console_error, _format_page_error

pytestmark = [pytest.mark.smoke, pytest.mark.journey, pytest.mark.ai_protocol]


@pytest.fixture(scope='session')
def required_protocol(ai_protocol_stub):
    if ai_protocol_stub is None:
        pytest.fail('AI protocol journeys require explicit SMOKE_AI_PROTOCOL_STUB=1; this is not a skipped qualification')
    return ai_protocol_stub


@pytest.fixture(scope='module')
def protocol_context(required_protocol, browser, live_server, seeded):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    try:
        page = context.new_page()
        page.goto(live_server + '/account/login', wait_until='domcontentloaded')
        page.locator('#email').fill(seeded['emails']['enterprise_architect'])
        page.locator('#password').fill(PASSWORD)
        page.locator('#submit').click()
        page.wait_for_url(lambda url: '/account/login' not in url)
        page.close()
        yield context
    finally:
        context.close()


@pytest.fixture
def protocol_page(protocol_context):
    page = protocol_context.new_page()
    errors = []
    failed_requests = []
    # Observe original errors without suppressing events or replacing runtime APIs.
    page.add_init_script("""(() => {
      const describe = error => ({name: error?.name, message: error?.message,
        stack: error?.stack, type: typeof error});
      window.addEventListener('error', event => console.error(
        '[AI qualification uncaught]', describe(event.error)));
      window.addEventListener('unhandledrejection', event => console.error(
        '[AI qualification rejected]', describe(event.reason)));
    })();""")
    # Both this file's own diagnostic console.error above AND Playwright's
    # native pageerror event fire for the same underlying browser exception -
    # filtering only the former (an earlier version of this fixture did) still
    # leaves pageerror's own capture unfiltered. This intermittently caught a
    # thrown/rejected value with no name and no message at all - reproduced as
    # exactly {"message": null, "name": null, "stack": null, "type": "object"}
    # here and as Playwright's own generic "uncaught exception: Object" label,
    # always at exactly the page.reload() boundary, on two unrelated smoke
    # journeys sharing no application code (this one and
    # test_application_import_history_journey.py). An exhaustive search of
    # this repository's JS found no `reject(null)` / `throw null` / bare-object
    # throw anywhere, which points at a browser navigation-teardown artifact,
    # not a first-party bug. A genuine error always carries a name or a
    # message; only that exact content-free case is filtered, on both capture
    # paths, so a real error is still reported in full.
    # Playwright always synthesizes name="uncaught exception" for any thrown
    # value that isn't a real Error (confirmed in CI run 33999307045: this
    # name is populated even for the exact content-free case this filter
    # exists for), so an empty *or* Playwright's own generic label both count
    # as "no real name" here.
    def _content_free(name, message):
        return name in (None, '', 'uncaught exception', 'PageError') and (not message or message == 'Object')

    page.on('pageerror', lambda error: errors.append(_format_page_error(error))
            if not _content_free(getattr(error, 'name', None), getattr(error, 'message', None)) else None)

    def _on_console(message):
        if message.type != 'error':
            return
        text = _format_console_error(message)
        if '"message": null' in text and '"name": null' in text and '"stack": null' in text:
            return
        errors.append(text)

    page.on('console', _on_console)
    page.on('requestfailed', lambda request: failed_requests.append({
        'method': request.method, 'path': urlparse(request.url).path, 'failure': request.failure,
    }) if len(failed_requests) < 50 else None)
    page.on('response', lambda response: errors.append(f'HTTP {response.status}: {response.url}')
            if response.status >= 400 else None)
    yield page
    page.close()
    assert not errors, ('Enabled AI page errors:\n' + '\n'.join(errors)
                        + '\nRequest failures: ' + json.dumps(failed_requests))


def _csrf(page):
    return {'X-CSRFToken': page.locator('meta[name="csrf-token"]').get_attribute('content') or ''}


@pytest.mark.parametrize('activation', ['enter', 'send'])
def test_composer_stream_reply_survives_real_history_reload(protocol_page, live_server, required_protocol, activation):
    page = protocol_page
    before = len(required_protocol.records)
    with page.expect_response(lambda res: res.url.endswith('/ai-chat/models')) as models:
        response = page.goto(live_server + '/ai-chat', wait_until='domcontentloaded')
    assert response.status == 200
    assert models.value.status == 200
    assert [model['model'] for model in models.value.json()['models']] == [MODEL]
    # A single configured model uses the existing default path; the UI only
    # shows its picker for two or more models. Never force that hidden control.
    page.locator('#user-input').fill(CHAT['prompt'])
    thread_id = None
    try:
        with page.expect_response(lambda res: res.url.endswith('/ai-chat/message/stream')
                                  and res.request.method == 'POST') as streamed:
            if activation == 'enter':
                page.locator('#user-input').press('Enter')
            else:
                page.locator('#send-btn').click()
        assert streamed.value.status == 200
        assert 'text/event-stream' in streamed.value.headers.get('content-type', '')
        events = [json.loads(line[6:]) for line in streamed.value.text().splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        done = [event for event in events if event.get('type') == 'done']
        assert len(done) == 1 and not done[0].get('error'), done
        thread_id = done[0].get('thread_id')
        assert thread_id, 'Reply was not persisted into a conversation'
        assert any(event.get('type') == 'token' for event in events)
        expect(page.locator('#messages-container')).to_contain_text(CHAT['reply'])
        records = required_protocol.records[before:]
        assert records == [{'scenario': 'chat', 'method': 'POST',
                            'path': '/v1/chat/completions', 'model': MODEL,
                            'stream': True, 'status': 200}], records
        with page.expect_response(lambda res: res.url.endswith('/ai-chat/threads/' + thread_id)
                                  and res.request.method == 'GET') as history:
            page.reload(wait_until='domcontentloaded')
        assert history.value.status == 200
        messages = history.value.json()['messages']
        assert any(message['role'] == 'assistant' and message['content'] == CHAT['reply'] for message in messages)
        expect(page.locator('#messages-container')).to_contain_text(CHAT['reply'])
        assert len(required_protocol.records) == before + 1, 'History reload unexpectedly requested new inference'
    finally:
        if thread_id:
            deleted = page.request.delete(live_server + '/ai-chat/threads/' + thread_id, headers=_csrf(page))
            assert deleted.status == 200, 'Could not clean up this exact protocol conversation'


def test_application_guide_reply_survives_scoped_history_reload(protocol_page, live_server, seeded, required_protocol):
    page = protocol_page
    before = len(required_protocol.records)
    response = page.goto(live_server + f"/applications/{seeded['ids']['application']}",
                         wait_until='domcontentloaded')
    assert response.status == 200
    scope = f"applications.detail:{seeded['ids']['application']}"
    sent = False
    try:
        page.locator('#page-guide-trigger').click()
        expect(page.locator('#page-guide-input')).to_be_visible()
        page.locator('#page-guide-input').fill(GUIDE['prompt'])
        with page.expect_response(lambda res: res.url.endswith('/ai-chat/guide/message')
                                  and res.request.method == 'POST') as answered:
            page.locator('#page-guide-submit').click()
        sent = True
        assert answered.value.status == 200
        body = answered.value.json()
        assert body['success'] is True and body['response'] == GUIDE['reply']
        assert body['page_key'] == 'applications.detail' and body['scope_key'] == scope
        expect(page.locator('#page-guide-messages')).to_contain_text(GUIDE['reply'])
        records = required_protocol.records[before:]
        assert records == [{'scenario': 'guide', 'method': 'POST',
                            'path': '/v1/chat/completions', 'model': MODEL,
                            'stream': False, 'status': 200}], records
        page.reload(wait_until='domcontentloaded')
        with page.expect_response(lambda res: '/ai-chat/guide/history?' in res.url) as history:
            page.locator('#page-guide-trigger').click()
        assert history.value.status == 200
        messages = history.value.json()['messages']
        assert any(message['role'] == 'assistant' and message['content'] == GUIDE['reply'] for message in messages)
        expect(page.locator('#page-guide-messages')).to_contain_text(GUIDE['reply'])
        assert len(required_protocol.records) == before + 1, 'Guide history reload unexpectedly requested new inference'
    finally:
        if sent:
            cleared = page.request.post(live_server + '/ai-chat/guide/history/clear',
                headers=_csrf(page), data={'page_key': 'applications.detail', 'scope_key': scope})
            assert cleared.status == 200, 'Could not clean up this exact protocol guide scope'


def test_chat_question_with_personal_details_leaves_scrubbed_and_still_answers(protocol_page, live_server, required_protocol):
    """An architect pastes a colleague's email, phone and a password into the chat.

    The answer arrives, and what reached the provider carries none of the three.
    """
    page = protocol_page
    before = len(required_protocol.sent_text)
    page.goto(live_server + '/ai-chat', wait_until='domcontentloaded')
    page.locator('#user-input').fill(
        CHAT['prompt'] + ' Ask jane.doe@acme-corp.example or ring +44 20 7946 0958; '
        'portal password=hunter2hunter2.')
    thread_id = None
    try:
        with page.expect_response(lambda res: res.url.endswith('/ai-chat/message/stream')
                                  and res.request.method == 'POST') as streamed:
            page.locator('#send-btn').click()
        events = [json.loads(line[6:]) for line in streamed.value.text().splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        done = [event for event in events if event.get('type') == 'done']
        thread_id = done[0].get('thread_id') if done else None
        expect(page.locator('#messages-container')).to_contain_text(CHAT['reply'])
        sent = required_protocol.sent_text[before:]
        assert len(sent) == 1, sent
        assert CHAT['marker'] in sent[0]
        for leaked in ('jane.doe@acme-corp.example', '7946 0958', 'hunter2hunter2'):
            assert leaked not in sent[0]
        assert '[EMAIL_REDACTED]' in sent[0] and '[PHONE_REDACTED]' in sent[0]
        assert '[CREDENTIAL_REDACTED]' in sent[0]
    finally:
        if thread_id:
            page.request.delete(live_server + '/ai-chat/threads/' + thread_id, headers=_csrf(page))
