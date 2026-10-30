"""Actual chat assets and native fetch against a controlled loopback HTTP boundary.

The core bundle is rendered in memory by the repository build function, so this
file does not overwrite generated assets owned by the main build. No provider,
database, fetch replacement, retry or blanket console-error exemption is used.
"""
import json
import mimetypes
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from scripts.build_js import BUNDLES, render
from tests.chat_opening_fixture import APPLICATIONS, APPLICATIONS_PATH, open_chat_options
from tests.test_ai_model_selector_display import ROOT, html


def _document():
    document = html()
    domains = {key: {'name': key.title(), 'description': 'Synthetic context', 'icon': 'network', 'color': 'primary'}
               for key in ('architecture', 'technology')}
    personas = {'fixture_architect': {'name': 'Fixture architect', 'description': 'Synthetic persona',
                                     'default_domain': 'architecture', 'sample_prompts': []}}
    document = document.replace('window.domainConfig = {};', 'window.domainConfig = ' + json.dumps(domains) + ';')
    document = document.replace('window.personaConfig = {};', 'window.personaConfig = ' + json.dumps(personas) + ';')
    document = document.replace('window.defaultChatPersona = "";', 'window.defaultChatPersona = "fixture_architect";')
    for selector, options in [('persona-selector', '<option value="fixture_architect" selected>Fixture architect</option>'),
                              ('domain-selector', '<option value="architecture">Architecture</option><option value="technology">Technology</option>')]:
        document = re.sub(r'(<select\b[^>]*\bid="' + selector + r'"[^>]*>)',
                          lambda match: match[1] + options, document)
    return document


@pytest.fixture(scope='module', params=['chromium', 'firefox', 'webkit'])
def lifecycle_browser(request):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch()
        yield browser
        browser.close()


@pytest.fixture
def context_page(lifecycle_browser):
    release, started = Event(), Event()
    state = SimpleNamespace(mode='normal', requests=[], documents=0, errors=[], page_errors=[], failures=[],
                            responses=[], disconnects=[], held_finished=Event(), posts=[])
    document = _document()
    bundle = render('core-admin.js', BUNDLES['core-admin.js'])

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            # Socket close is an observable outcome of intentional browser cancellation.
            import sys
            state.disconnects.append(type(sys.exception()).__name__)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def respond(self, body, content_type='application/json', status=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            if isinstance(body, str):
                body = body.encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError) as error:
                state.disconnects.append(type(error).__name__)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                state.documents += 1
                return self.respond(document, 'text/html')
            if path == '/static/js/bundles/core-admin.js':
                return self.respond(bundle, 'text/javascript')
            if path.startswith('/static/'):
                target = (ROOT / 'app' / path[1:]).resolve()
                if not target.is_relative_to((ROOT / 'app/static').resolve()):
                    return self.respond({}, status=400)
                return self.respond(target.read_bytes(), mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            if path.startswith('/ai-chat/context/'):
                state.requests.append(path)
                held = state.mode == 'delayed' and state.documents == 1 and path.endswith('/architecture')
                if held:
                    started.set()
                    release.wait(15)
                if state.mode == 'network':
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if state.mode == 'http':
                    return self.respond({'error': 'Synthetic context unavailable'}, status=503)
                if state.mode == 'malformed':
                    field = 'architecture_elements' if path.endswith('/architecture') else 'technology_stacks'
                    return self.respond({'success': True, 'context': {field: None}})
                if state.mode == 'logical_error':
                    return self.respond({'success': True, 'context': {'error': 'Synthetic context query failure'}})
                if path.endswith('/architecture'):
                    elements = [] if state.mode == 'empty' else [{'id': 51, 'name': 'Recorded architecture fixture', 'type': 'ApplicationComponent', 'layer': 'application', 'relationships': 0}]
                    body = {'success': True, 'context': {'architecture_elements': elements,
                            'total_elements': len(elements), 'detail_count': len(elements)}}
                else:
                    stacks = [] if state.mode == 'empty' else [{'id': 61, 'name': 'Newest technology fixture',
                                                               'description': 'Recorded stack description', 'technologies': []}]
                    body = {'success': True, 'context': {'technology_stacks': stacks, 'total_stacks': len(stacks)}}
                self.respond(body)
                if held:
                    state.held_finished.set()
                return
            if path == '/fixture/pending':
                started.set()
                release.wait(15)
                return self.respond({'success': True})
            bodies = {
                '/ai-chat/models': {'success': True, 'models': [{'model': 'fixture', 'display_name': 'Fixture'}]},
                '/ai-chat/threads': {'threads': []}, '/ai-chat/approvals/queue': {'success': True, 'approvals': []},
                '/ai-chat/recommendations': {'alerts': [], 'recommendations': [], 'summary': {'total': 0}},
                '/account/session/keepalive': {'success': True},
                APPLICATIONS_PATH: APPLICATIONS,
            }
            if path in bodies:
                return self.respond(bodies[path])
            if path == '/favicon.ico':
                return self.respond(b'', 'image/x-icon', 204)
            return self.respond({}, status=404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state.posts.append({'path': self.path, 'csrf': self.headers.get('X-CSRFToken'), 'body': json.loads(body)})
            return self.respond({'success': True})

    server = Server(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = lifecycle_browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda error: state.page_errors.append(str(error)))
    page.on('console', lambda message: state.errors.append(message.text) if message.type == 'error' else None)
    page.on('requestfailed', lambda request: state.failures.append({'path': urlsplit(request.url).path, 'failure': request.failure}))
    page.on('response', lambda response: state.responses.append(response.status) if response.status >= 400 else None)

    def start(mode='normal'):
        state.mode = mode
        page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='domcontentloaded')
        page.wait_for_function("document.querySelector('#model-selector-empty-note').textContent.includes('platform default')")
        return page

    yield SimpleNamespace(start=start, page=page, state=state, release=release, started=started)
    release.set()
    context.close()
    server.shutdown()
    server.server_close()
    thread.join(5)
    assert not thread.is_alive()
    assert not state.page_errors, state.page_errors
    if state.mode not in ('http', 'network'):
        assert not state.errors, state.errors


def test_initial_context_loads_once_and_renders_real_envelope(context_page):
    page = context_page.start()
    expect(page.locator('#domain-context')).to_contain_text('Recorded architecture fixture')
    assert context_page.state.requests == ['/ai-chat/context/architecture']
    assert not context_page.state.errors


def test_reload_cancels_owned_pending_context_without_false_error(context_page):
    page = context_page.start('delayed')
    assert context_page.started.wait(5)
    page.reload(wait_until='domcontentloaded')
    context_page.release.set()
    expect(page.locator('#domain-context')).to_contain_text('Recorded architecture fixture')
    assert not context_page.state.errors, context_page.state.errors
    assert context_page.state.responses == []


def test_domain_switch_cancels_old_load_and_keeps_newest_context(context_page):
    page = context_page.start('delayed')
    assert context_page.started.wait(5)
    open_chat_options(page)
    page.locator('#domain-selector').select_option('technology')
    expect(page.locator('#domain-context')).to_contain_text('Newest technology fixture')
    expect(page.locator('#domain-context')).to_contain_text('Technology Stacks')
    expect(page.locator('#domain-context [data-action="select-context"]')).to_have_count(0)
    context_page.release.set()
    assert context_page.state.held_finished.wait(5)
    page.wait_for_timeout(150)  # Allow the explicitly released stale response to reach the browser.
    expect(page.locator('#domain-context')).to_contain_text('Newest technology fixture')
    expect(page.locator('#domain-context')).not_to_contain_text('Recorded architecture fixture')
    assert not context_page.state.errors, context_page.state.errors


@pytest.mark.parametrize('domain', ['architecture', 'technology'])
@pytest.mark.parametrize('mode', ['empty', 'malformed', 'logical_error', 'http', 'network'])
def test_context_empty_and_failure_states_remain_distinct(context_page, mode, domain):
    page = context_page.start(mode if domain == 'architecture' else 'normal')
    if domain == 'technology':
        expect(page.locator('#domain-context')).to_contain_text('Recorded architecture fixture')
        context_page.state.mode = mode
        open_chat_options(page)
        page.locator('#domain-selector').select_option('technology')
    panel = page.locator('#domain-context')
    if mode == 'empty':
        expect(panel).to_contain_text('so the answer uses your whole portfolio')
        assert not context_page.state.errors
    else:
        expect(panel).to_contain_text("Couldn't load context")
        if mode in ('http', 'network'):
            assert context_page.state.errors, 'Real transport failures must remain observable'
            expect(page.locator('#platform-toast-container')).to_contain_text(
                'Synthetic context unavailable' if mode == 'http' else 'Network error')


@pytest.mark.parametrize('null_reason', [False, True])
def test_explicit_cancellation_rejects_and_keeps_loading_and_csrf_contracts(context_page, null_reason):
    page = context_page.start('empty')
    expect(page.locator('#domain-context')).to_contain_text('so the answer uses your whole portfolio')
    page.evaluate("""() => {
      // The layout fixture has no loading store; use a real Alpine store to observe the API balance.
      Alpine.store('loading', {pending: 0, start() {this.pending++;}, stop() {this.pending--;}});
      window.fixtureController = new AbortController();
      window.fixtureOutcome = null;
      Platform.fetch('/fixture/pending', {signal: fixtureController.signal}).then(
        () => {window.fixtureOutcome = 'resolved';}, error => {window.fixtureOutcome = error === null ? 'null cancellation' : error.name;});
    }""")
    assert context_page.started.wait(5)
    page.evaluate('fixtureController.abort(null)' if null_reason else 'fixtureController.abort()')
    page.wait_for_function("window.fixtureOutcome !== null", timeout=3000)
    # WebKit preserves AbortError for abort(null); Chromium/Firefox preserve null.
    allowed_rejections = ('null cancellation', 'AbortError') if null_reason else ('AbortError',)
    assert page.evaluate('fixtureOutcome') in allowed_rejections
    assert page.evaluate("Alpine.store('loading').pending") == 0
    assert not context_page.state.errors
    page.evaluate("() => Platform.fetch.post('/fixture/write', {name: 'Synthetic mutation'})")
    assert context_page.state.posts == [{'path': '/fixture/write', 'csrf': 'test-token', 'body': {'name': 'Synthetic mutation'}}]
    assert page.evaluate("Alpine.store('loading').pending") == 0
