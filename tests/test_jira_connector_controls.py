"""Real Jira template/core assets with a synthetic loopback connection endpoint.

No Jira service, configuration save, credential storage or application database.
The base-layout wrapper substitutes navigation/auth context, not page controls.
"""
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright


from tests.render_context import app_template_context

ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = '/admin/connectors/jira'
TEST_PATH = PAGE_PATH + '/test'
INPUT = {'instance_url': 'https://jira-fixture.invalid',
         'email': 'synthetic@example.invalid', 'api_token': 'synthetic-test-value'}


def jira_html():
    base = '<!doctype html><html><head><meta charset="utf-8">'
    base += '<meta name="csrf-token" content="synthetic-csrf-value">'
    for asset in ['css/shadcn_tokens.css', 'css/tailwind-output.css', 'css/app.css']:
        base += f'<link rel="stylesheet" href="/static/{asset}">'
    for asset in ['vendor/purify.min.js', 'vendor/lucide.min.js', 'js/bundles/core-admin.js']:
        base += f'<script src="/static/{asset}"></script>'
    base += '</head><body><main>{% block content %}{% endblock %}</main></body></html>'
    env = Environment(loader=ChoiceLoader([
        DictLoader({'layouts/admin_base.html': base}), FileSystemLoader(ROOT / 'app/templates')
    ]), autoescape=True)
    routes = {'admin.index': '/admin', 'm365_connector.jira_config_save': PAGE_PATH,
              'm365_connector.jira_config_test': TEST_PATH}
    env.globals.update(url_for=lambda endpoint: routes[endpoint],
                       csrf_token=lambda: 'synthetic-csrf-value',
                       get_flashed_messages=lambda **kwargs: [])
    return env.get_template('admin/connectors/jira.html').render(cfg={}, connector=None, **app_template_context())


@pytest.fixture(scope='module', params=['chromium', 'firefox', 'webkit'])
def jira_browser(request):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch()
        yield browser
        browser.close()


@pytest.mark.parametrize('viewport', [{'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}],
                         ids=['desktop', 'mobile'])
@pytest.mark.parametrize('outcome', ['success', 'http_error', 'connection_rejected'])
@pytest.mark.parametrize('gesture', ['click', 'dblclick'], ids=['single-click', 'double-click'])
def test_connection_feedback_pending_cleanup_and_double_click(jira_browser, viewport, outcome, gesture):
    document = jira_html()
    started, release = Event(), Event()
    writes, unexpected, errors = [], [], []
    result_message = {'success': 'Synthetic connection succeeded', 'http_error': 'Synthetic request failed',
                      'connection_rejected': 'Synthetic connection rejected'}[outcome]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, body, content_type='application/json', status=200):
            if isinstance(body, dict):
                body = json.dumps(body)
            if isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == PAGE_PATH:
                return self.respond(document, 'text/html')
            if path.startswith('/static/'):
                target = (ROOT / 'app' / path.lstrip('/')).resolve()
                if target.is_relative_to((ROOT / 'app/static').resolve()) and target.is_file():
                    return self.respond(target.read_bytes(), mimetypes.guess_type(str(target))[0]
                                        or 'application/octet-stream')
            if path == '/account/session/keepalive':
                return self.respond({'success': True})
            if path == '/favicon.ico':
                return self.respond(b'', 'image/x-icon', 204)
            unexpected.append(('GET', path))
            return self.respond({}, status=404)

        def do_POST(self):
            path = urlsplit(self.path).path
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if path == '/account/session/keepalive':
                return self.respond({'success': True})
            if path != TEST_PATH:
                unexpected.append(('POST', path))
                return self.respond({}, status=405)
            writes.append({'path': path, 'body': json.loads(body),
                           'csrf': self.headers.get('X-CSRFToken'),
                           'content_type': self.headers.get('Content-Type')})
            started.set()
            if not release.wait(10):
                return self.respond({'error': 'Synthetic fixture timed out'}, status=500)
            return self.respond({'status': 'ok' if outcome == 'success' else 'error',
                                 'message': result_message, 'error': result_message},
                                status=400 if outcome == 'http_error' else 200)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    context = jira_browser.new_context(viewport=viewport)
    try:
        def reject_external_request(route):
            unexpected.append(('EXTERNAL', route.request.url))
            return route.abort()

        # Keep native loopback requests independent of Python's event pump:
        # started.wait below must not block a route callback needed to send them.
        context.route(re.compile(r'^(?!' + re.escape(origin + '/') + r').*'), reject_external_request)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(origin + PAGE_PATH, wait_until='load')
        assert errors == [], f'Current Jira page failed to parse: {errors}'
        for field, value in INPUT.items():
            page.locator('#' + field).fill(value)
        button = page.locator('#test-jira-btn')
        getattr(button, gesture)()
        assert started.wait(3), 'Ordinary button gesture did not send a connection test'
        expect(button).to_be_disabled()
        expect(button).to_have_text('Testing…')
        result = page.locator('#jira-test-result')
        expect(result).to_be_visible()
        expect(result).to_have_text('Connecting to Jira…')
        assert writes == [{'path': TEST_PATH, 'body': INPUT, 'csrf': 'synthetic-csrf-value',
                           'content_type': 'application/json'}]
        release.set()
        expect(result).to_have_text(result_message)
        expect(button).to_be_enabled()
        expect(button).to_have_text('Test Connection')
        assert len(writes) == 1, 'Double-click sent duplicate requests'
        assert errors == []
        assert unexpected == []
    finally:
        release.set()
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
