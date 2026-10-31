"""Real chat template and shipped scripts; only HTTP responses/layout are fixtures.

No database or provider. A loopback fixture and restrictive CSP deny external
requests; normal UI input exercises the composer and production SSE renderer.
Ordinary Enter and Send clicks exercise the shared submit path. All three
engines remain enabled by default, without skips or expected failures.
"""
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright

from tests.chat_opening_fixture import (
    APPLICATIONS, APPLICATIONS_PATH, close_chat_options, open_chat_options,
)
from tests.render_context import app_template_context

ROOT = Path(__file__).resolve().parents[1]


def html():
    scripts = ['vendor/purify.min.js', 'vendor/lucide.min.js',
               'js/bundles/core-admin.js', 'js/ui/modal.js']
    deferred = ['vendor/alpine-focus.min.js', 'vendor/alpine-intersect.min.js',
                'vendor/alpine-collapse.min.js', 'js/csp/csp-evaluator.js',
                'js/csp/alpine-csp-adapter.js', 'vendor/alpine.min.js']
    base = '<!doctype html><html><head><meta charset="utf-8">'
    base += '<meta name="csrf-token" content="test-token">'
    for path in ['css/shadcn_tokens.css', 'css/tailwind-output.css', 'css/app.css']:
        base += f'<link rel="stylesheet" href="/static/{path}">'
    base += ''.join(f'<script src="/static/{p}"></script>' for p in scripts)
    base += ''.join(f'<script defer src="/static/{p}"></script>' for p in deferred)
    base += ('{% block extra_css %}{% endblock %}</head><body>'
             '{% block content %}{% endblock %}'
             '{% block scripts %}{% endblock %}</body></html>')
    env = Environment(loader=ChoiceLoader([
        DictLoader({'layouts/admin_base.html': base}),
        FileSystemLoader(ROOT / 'app/templates')]), autoescape=True)
    return env.get_template('ai_chat/index.html').render(
        **app_template_context(),
        llm_configured=True, domain_config={},
        persona_config={'categories': {}, 'personas': {}}, default_chat_persona='',
        chat_persona_preference_key='test-model-display', csrf_token=lambda: 'test-token',
        url_for=lambda endpoint, **kw: '/static/' + kw['filename'] if endpoint == 'static' else '/')


@pytest.fixture(scope='module')
def browser(request):
    with sync_playwright() as pw:
        instance = getattr(pw, request.param).launch()
        yield instance
        instance.close()


@pytest.fixture
def launch(browser):
    contexts = []
    servers = []
    errors = []
    unexpected = []

    def start(models, failure=False, stream_fault=False):
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        contexts.append(context)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('requestfailed', lambda request: errors.append(
            {'path': urlsplit(request.url).path, 'failure': request.failure}))
        payloads = []
        document = html()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass

            def respond(self, body, content_type='application/json', status=200):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body)
                if isinstance(body, str):
                    body = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type + '; charset=utf-8')
                self.send_header('Content-Security-Policy',
                    "default-src 'self' data:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                    "style-src 'self' 'unsafe-inline'; connect-src 'self'")
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split('?')[0]
                if path.startswith('/static/'):
                    target = (ROOT / 'app' / path[1:]).resolve()
                    if not target.is_relative_to((ROOT / 'app/static').resolve()):
                        return self.respond({}, status=400)
                    return self.respond(target.read_bytes(),
                        mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
                if path == '/':
                    return self.respond(document, 'text/html')
                if path == '/ai-chat/models':
                    return self.respond({'success': not failure, 'models': models,
                        **({'error': 'Fixture model-list outage'} if failure else {})},
                        status=503 if failure else 200)
                if path == '/ai-chat/threads':
                    return self.respond({'threads': []})
                if path == '/ai-chat/approvals/queue':
                    return self.respond({'success': True, 'approvals': []})
                if path == '/account/session/keepalive':
                    return self.respond({'success': True})
                if path.startswith('/ai-chat/context/'):
                    return self.respond({'elements': [], 'applications': []})
                if path == APPLICATIONS_PATH:
                    return self.respond(APPLICATIONS)
                if path == '/ai-chat/recommendations':
                    return self.respond({'alerts': [], 'recommendations': [],
                                         'summary': {'total': 0}})
                if path == '/favicon.ico':
                    return self.respond(b'', 'image/x-icon', status=204)
                unexpected.append(self.path)
                self.respond({}, status=404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if self.path == '/account/session/keepalive':
                    return self.respond({'success': True})
                if self.path == '/ai-chat/estate-answer':
                    # The chat asks the records first; this organisation names nothing in them.
                    return self.respond({'success': True, 'found': False})
                if self.path == '/ai-chat/message/stream':
                    payloads.append(json.loads(body))
                    if stream_fault:
                        # The no-provider gate's answer: a configuration fault, not a transient failure.
                        return self.respond({'error': 'service_unavailable', 'feature': 'chat',
                                             'message': "AI feature 'chat' is not available. LLM provider must be configured."},
                                            status=503)
                    events = [{'type': 'token', 'text': 'Fixture response.'},
                              {'type': 'done', 'response': 'Fixture response.', 'domain': 'general'}]
                    return self.respond(''.join('data: ' + json.dumps(e) + '\n\n'
                                               for e in events), 'text/event-stream')
                unexpected.append(self.path)
                self.respond({}, status=404)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        base = f'http://127.0.0.1:{server.server_port}'

        page.on('request', lambda request: unexpected.append(request.url)
                if not request.url.startswith(base + '/') else None)
        page.goto(base + '/')
        open_chat_options(page)
        page.on('framenavigated', lambda frame: errors.append(
            {'unexpected_navigation': urlsplit(frame.url).path + '?' + urlsplit(frame.url).query})
            if frame == page.main_frame else None)
        return page, payloads

    yield start
    for context in contexts:
        context.close()
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert errors == []
    assert unexpected == []


@pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
@pytest.mark.parametrize('count', [0, 1, 2])
def test_model_count_note(launch, count):
    models = [{'model': f'fixture-{n}', 'display_name': f'Fixture {n}',
               'provider': 'openai', 'recommended_for': [], 'is_fallback': False,
               'fallback_order': 0, 'test_status': None} for n in range(count)]
    page, _ = launch(models)
    note = page.locator('#model-selector-empty-note')
    selector = page.locator('#model-selector')
    if count < 2:
        expect(selector).to_be_hidden()
        expect(note).to_be_visible()
        if count == 0:
            expect(note).to_have_text('No AI models are configured.')
        else:
            expect(note).to_contain_text('One AI model is configured')
            expect(note).not_to_contain_text('No AI models')
        expect(selector.locator('option')).to_have_count(1)
    else:
        expect(selector).to_be_visible()
        expect(note).to_be_hidden()
        expect(selector.locator('option')).to_have_count(3)


@pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
def test_failed_loading_is_not_reported_as_no_models(launch):
    page, _ = launch([], failure=True)
    expect(page.locator('#model-selector')).to_be_hidden()
    note = page.locator('#model-selector-empty-note')
    expect(note).to_contain_text('model list could not be loaded')
    expect(note).to_contain_text('Fixture model-list outage')
    expect(note).not_to_contain_text('No AI models')


@pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
@pytest.mark.parametrize('count,failure', [(0, False), (1, False), (2, False), (0, True)])
def test_default_composer_preserves_automatic_model(launch, count, failure):
    models = [{'model': f'fixture-{n}', 'display_name': f'Fixture {n}', 'provider': 'openai',
               'recommended_for': [], 'is_fallback': False, 'fallback_order': 0,
               'test_status': None} for n in range(count)]
    page, payloads = launch(models, failure=failure)
    if failure:
        expect(page.locator('#model-selector-empty-note')).to_contain_text('could not be loaded')
    elif count < 2:
        expect(page.locator('#model-selector-empty-note')).to_be_visible()
    else:
        expect(page.locator('#model-selector')).to_be_visible()
    close_chat_options(page)
    initial_url = page.url
    page.locator('#user-input').fill('Check model display fixture.')
    page.locator('#user-input').press('Enter')
    expect(page.locator('#messages-container .message-content').last).to_contain_text('Fixture response.')
    assert len(payloads) == 1
    assert payloads[0]['model'] is None
    assert payloads[0]['message'] == 'Check model display fixture.'
    assert page.url == initial_url


@pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
def test_multiple_models_preserve_explicit_selection(launch):
    page, payloads = launch([
        {'model': 'fixture-a', 'display_name': 'Fixture A', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None},
        {'model': 'fixture-b', 'display_name': 'Fixture B', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None}])
    page.get_by_label('AI model', exact=True).select_option('fixture-b')
    close_chat_options(page)
    page.locator('#user-input').fill('Use the selected fixture model.')
    page.locator('#user-input').press('Enter')
    expect(page.locator('#messages-container')).to_contain_text('Fixture response.')
    assert len(payloads) == 1
    assert payloads[0]['model'] == 'fixture-b'


@pytest.mark.parametrize('browser', ['chromium', 'firefox', 'webkit'], indirect=True)
def test_ordinary_send_click_uses_same_stream_without_navigation(launch):
    page, payloads = launch([
        {'model': 'fixture-0', 'display_name': 'Fixture 0', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None}])
    expect(page.locator('#model-selector-empty-note')).to_contain_text('One AI model is configured')
    close_chat_options(page)
    initial_url = page.url
    page.locator('#user-input').fill('Send click fixture.')
    page.locator('#send-btn').click()
    expect(page.locator('#messages-container .message-content').last).to_contain_text('Fixture response.')
    assert payloads == [{'message': 'Send click fixture.', 'domain': 'general',
                         'element_id': None, 'context_type': None, 'persona': None,
                         'model': None, 'thread_id': None}]
    assert page.url == initial_url
