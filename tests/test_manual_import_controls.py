"""Actual manual-import modal and scripts; native HTTP fixture, no database.

The surrounding page is synthetic. Fields, sanitizer, modal, Alpine evaluator,
fetch wrapper and controls are production assets, never replaced at runtime.
"""
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = '/applications/import-manual'
MAP_ENDPOINT = '/applications/api/comprehensive-auto-map'


def manual_html():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=True)
    from app.services.application_status import status_options
    env.globals.update(csrf_token=lambda: 'synthetic-token', status_options=status_options,
                       url_for=lambda endpoint, **kw: '/static/' + kw['filename'])
    html = '<!doctype html><html><head><meta charset="utf-8"><meta name="csrf-token" content="synthetic-token">'
    for asset in ['css/shadcn_tokens.css', 'css/tailwind-output.css', 'css/app.css']:
        html += f'<link rel="stylesheet" href="/static/{asset}">'
    for asset in ['vendor/purify.min.js', 'vendor/lucide.min.js', 'js/bundles/core-admin.js', 'js/ui/modal.js']:
        html += f'<script src="/static/{asset}"></script>'
    for asset in ['vendor/alpine-focus.min.js', 'vendor/alpine-intersect.min.js',
                  'vendor/alpine-collapse.min.js', 'js/csp/csp-evaluator.js',
                  'js/csp/alpine-csp-adapter.js', 'vendor/alpine.min.js']:
        html += f'<script defer src="/static/{asset}"></script>'
    return html + ('</head><body><main x-data><button type="button" '
                   'data-modal-open="application-import-modal">Open Import</button>') + env.get_template(
                       'application_mgmt/application_import_modal.html').render() + '</main></body></html>'


@pytest.fixture(scope='module', params=['chromium', 'firefox', 'webkit'])
def manual_browser(request):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch()
        yield browser
        browser.close()


@pytest.fixture(params=[{'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}], ids=['desktop', 'mobile'])
def manual_page(manual_browser, request):
    document = manual_html()
    release, started, map_release, map_started = Event(), Event(), Event(), Event()
    state = {'posts': [], 'maps': [], 'errors': [], 'console_errors': [], 'unexpected': [], 'status': 200,
             'map_status': 200, 'map_response': {'success': True, 'process_mappings_created': 1,
                                              'archimate_elements_created': 0,
                                              'vendor_archimate_cloned': 0, 'vendor_matches_found': 0},
             'map_release': map_release, 'map_started': map_started,
             'response': {'success': True, 'created': 1, 'updated': 0, 'skipped': 0, 'failed': 0, 'errors': []},
             'release': release, 'started': started}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, body, content_type='application/json', status=200):
            if content_type == 'application/json':
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
            if path == '/':
                return self.respond(document, 'text/html')
            if path.startswith('/static/'):
                target = (ROOT / 'app' / path.lstrip('/')).resolve()
                if target.is_relative_to((ROOT / 'app/static').resolve()) and target.is_file():
                    return self.respond(target.read_bytes(), mimetypes.guess_type(str(target))[0]
                                        or 'application/octet-stream')
            if path == '/applications/import-fields':
                return self.respond({'fields': [{'value': 'name', 'label': 'Name'}], 'aliases': {}})
            if path == '/account/session/keepalive':
                return self.respond({'success': True})
            if path == '/favicon.ico':
                return self.respond(b'', 'image/x-icon', 204)
            state['unexpected'].append(('GET', path))
            return self.respond({}, status=404)

        def do_POST(self):
            path = urlsplit(self.path).path
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if path == '/account/session/keepalive':
                return self.respond({'success': True})
            if path == MAP_ENDPOINT:
                state['maps'].append({'body': json.loads(body), 'csrf': self.headers.get('X-CSRFToken')})
                map_started.set()
                if not map_release.wait(12):
                    return self.respond({'error': 'Fixture mapping not released'}, status=500)
                return self.respond(state['map_response'], status=state['map_status'])
            if path != ENDPOINT:
                state['unexpected'].append(('POST', path))
                return self.respond({}, status=405)
            state['posts'].append({'body': json.loads(body), 'csrf': self.headers.get('X-CSRFToken')})
            started.set()
            if not release.wait(12):
                return self.respond({'error': 'Fixture response not released'}, status=500)
            return self.respond(state['response'], status=state['status'])

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    context = manual_browser.new_context(viewport=request.param)
    try:
        def deny_external(route):
            state['unexpected'].append(('EXTERNAL', route.request.url))
            return route.abort()

        context.route(re.compile(r'^(?!' + re.escape(origin + '/') + r').*'), deny_external)
        page = context.new_page()
        page.on('pageerror', lambda error: state['errors'].append(str(error)))
        page.on('console', lambda message: state['console_errors'].append(
            {'text': message.text, 'url': message.location.get('url', '')}) if message.type == 'error' else None)
        page.goto(origin + '/', wait_until='load')
        yield page, state
    finally:
        release.set()
        map_release.set()
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert state['errors'] == []
    assert state['unexpected'] == []
    unexpected_console = []
    for error in state['console_errors']:
        expected_http_error = any(
            status >= 400 and error['url'] == origin + endpoint
            and ('Failed to load resource' in error['text'] or '400' in error['text'])
            for endpoint, status in [(ENDPOINT, state['status']), (MAP_ENDPOINT, state['map_status'])])
        if not expected_http_error:
            unexpected_console.append(error)
    assert unexpected_console == []


def open_manual(page):
    page.get_by_role('button', name='Open Import', exact=True).click()
    dialog = page.get_by_role('dialog', name='Import Applications', exact=True)
    dialog.get_by_role('button', name='Manual entry tab', exact=True).click()
    return dialog


def add_row(dialog, name='Synthetic import', kind='ERP'):
    dialog.get_by_role('button', name='Add manual entry row', exact=True).click()
    rows = dialog.locator('#manual-entry-tbody tr')
    row = rows.nth(rows.count() - 1)
    row.get_by_placeholder('APP ID', exact=True).fill('SYNTHETIC-01')
    row.get_by_placeholder('Application Name *', exact=True).fill(name)
    row.get_by_placeholder('Type', exact=True).fill(kind)
    row.locator('select').select_option('design')
    return row


def import_button(dialog):
    return dialog.get_by_role('button', name='Import manual entries', exact=True)


def acknowledge_import(page, close=False):
    result = page.get_by_role('dialog', name='Import saved', exact=True)
    expect(result).to_be_visible()
    with page.expect_navigation(wait_until='load'):
        result.get_by_role('button', name='Close' if close else 'Done — refresh applications', exact=True).click()


def test_normal_create_and_merge_payload(manual_page):
    page, state = manual_page
    for index, kind in enumerate(['ERP', 'CRM']):
        dialog = open_manual(page)
        add_row(dialog, kind=kind)
        state['release'].clear()
        state['started'].clear()
        state['response'].update(created=1 if index == 0 else 0, updated=index)
        import_button(dialog).click()
        assert state['started'].wait(3), 'Visible manual import did not send the request'
        expect(import_button(dialog)).to_be_disabled()
        state['release'].set()
        result = page.get_by_role('dialog', name='Import saved', exact=True)
        expect(result).to_contain_text('Created: 1' if index == 0 else 'Created: 0')
        expect(result).to_contain_text('Updated: 0' if index == 0 else 'Updated: 1')
        expect(result).to_contain_text('Auto-mapping was not requested')
        expect(page.locator('#manual-entry-tbody tr')).to_have_count(1)
        expect(page.locator('#application-import-modal [aria-label="Import manual entries"]')).to_be_disabled()
        acknowledge_import(page, close=index == 1)
        assert state['posts'][-1] == {'csrf': 'synthetic-token', 'body': {
            'applications': [{'app_id': 'SYNTHETIC-01', 'name': 'Synthetic import',
                              'component_type': kind, 'deployment_status': 'design'}],
            'duplicate_mode': 'update'}}
        assert len(state['posts']) == index + 1


@pytest.mark.parametrize('failure', ['http', 'logical', 'logical_without_message', 'empty_object', 'null'])
def test_failure_restores_button_and_allows_retry(manual_page, failure):
    page, state = manual_page
    dialog = open_manual(page)
    add_row(dialog)
    state['status'] = 400 if failure == 'http' else 200
    state['response'] = {'success': False}
    if failure in ['http', 'logical']:
        state['response']['error'] = 'Synthetic import failure'
    elif failure == 'empty_object':
        state['response'] = {}
    elif failure == 'null':
        state['response'] = None
    state['release'].set()
    for expected_requests in [1, 2]:
        with page.expect_response(lambda response: ENDPOINT in response.url):
            import_button(dialog).click()
        expect(import_button(dialog)).to_be_enabled()
        expect(import_button(dialog)).to_have_text('Import Applications')
        expect(dialog).to_be_visible()
        assert len(state['posts']) == expected_requests
    expect(page.locator('body')).to_contain_text('Synthetic import failure' if failure in ['http', 'logical']
                                                else 'Import failed')


def test_double_click_submits_once_while_pending(manual_page):
    page, state = manual_page
    dialog = open_manual(page)
    add_row(dialog)
    state['status'] = 400
    state['response'] = {'error': 'Synthetic import failure'}
    import_button(dialog).dblclick()
    assert state['started'].wait(3)
    expect(import_button(dialog)).to_be_disabled()
    assert len(state['posts']) == 1
    state['release'].set()
    expect(import_button(dialog)).to_be_enabled()
    assert len(state['posts']) == 1


def test_missing_name_is_explained_without_post(manual_page):
    page, state = manual_page
    dialog = open_manual(page)
    add_row(dialog, name='')
    import_button(dialog).click()
    confirm = page.get_by_role('dialog', name='Confirm', exact=True)
    expect(confirm).to_contain_text('missing required fields')
    confirm.get_by_role('button', name='Confirm', exact=True).click()
    expect(page.locator('body')).to_contain_text('No valid applications to import')
    expect(import_button(dialog)).to_be_enabled()
    assert state['posts'] == []


def test_partial_batch_keeps_submit_button_after_confirmation(manual_page):
    page, state = manual_page
    dialog = open_manual(page)
    add_row(dialog)
    add_row(dialog, name='')
    import_button(dialog).click()
    confirm = page.get_by_role('dialog', name='Confirm', exact=True)
    confirm.get_by_role('button', name='Confirm', exact=True).click()
    assert state['started'].wait(3)
    expect(import_button(dialog)).to_be_disabled()
    assert len(state['posts'][0]['body']['applications']) == 1
    state['release'].set()
    acknowledge_import(page)


def test_remove_row_button_removes_only_selected_row(manual_page):
    page, state = manual_page
    dialog = open_manual(page)
    first = add_row(dialog, name='First')
    add_row(dialog, name='Second')
    first.get_by_role('button', name='Remove', exact=True).click()
    rows = dialog.locator('#manual-entry-tbody tr')
    expect(rows).to_have_count(1)
    expect(rows.get_by_placeholder('Application Name *', exact=True)).to_have_value('Second')
    assert state['posts'] == []


@pytest.mark.parametrize('mapping_case', [
    'success', 'http_failure', 'logical_failure', 'partial_errors', 'rolled_back',
    'empty_object', 'null', 'negative_count', 'unsafe_error',
])
def test_checked_auto_map_retains_saved_result_until_acknowledged(manual_page, mapping_case):
    page, state = manual_page
    dialog = open_manual(page)
    add_row(dialog)
    dialog.locator('#auto-map-after-import-manual').check()
    dialog.locator('#import-map-capabilities-manual').uncheck()
    dialog.locator('#import-generate-archimate-manual').check()
    dialog.locator('#import-clone-vendor-manual').check()
    state['map_status'] = 400 if mapping_case == 'http_failure' else 200
    expected = 'Auto-mapping completed'
    if mapping_case == 'http_failure':
        state['map_response'] = {'error': 'Synthetic mapping failure'}
        expected = 'Auto-mapping failed: Synthetic mapping failure'
    elif mapping_case == 'logical_failure':
        state['map_response'] = {'success': False, 'error': 'Synthetic mapping rejection'}
        expected = 'Auto-mapping failed: Synthetic mapping rejection'
    elif mapping_case in ['partial_errors', 'rolled_back']:
        state['map_response'].update(process_mappings_created=2 if mapping_case == 'partial_errors' else 0,
                                     creation_errors=['Synthetic mapping creation failed'])
        expected = 'Auto-mapping reported errors'
    elif mapping_case == 'empty_object':
        state['map_response'] = {}
        expected = 'Auto-mapping outcome could not be confirmed'
    elif mapping_case == 'null':
        state['map_response'] = None
        expected = 'Auto-mapping outcome could not be confirmed'
    elif mapping_case == 'negative_count':
        state['map_response']['process_mappings_created'] = -1
        expected = 'Auto-mapping outcome could not be confirmed'
    elif mapping_case == 'unsafe_error':
        state['map_response'] = {'success': False, 'error': '<img src=x onerror=alert(1)> rejected'}
        expected = 'Auto-mapping failed: <img src=x onerror=alert(1)> rejected'
    state['release'].set()
    import_button(dialog).dblclick()
    assert state['map_started'].wait(3)
    expect(dialog).to_be_visible()
    expect(import_button(dialog)).to_be_disabled()
    assert len(state['posts']) == 1
    assert state['maps'] == [{'csrf': 'synthetic-token', 'body': {
        'max_applications': 1, 'map_capabilities': False, 'map_processes': True,
        'generate_archimate': True, 'clone_vendor_archimate': True, 'auto_create': True}}]
    state['map_release'].set()
    result = page.get_by_role('dialog', name='Import saved', exact=True)
    expect(result).to_be_visible()
    expect(result).to_contain_text('Created: 1')
    expect(result).to_contain_text('Updated: 0')
    expect(result).to_contain_text(expected)
    expect(page.locator('#application-import-modal [aria-label="Import manual entries"]')).to_be_disabled()
    expect(result.locator('img')).to_have_count(0)
    if mapping_case in ['partial_errors', 'rolled_back']:
        expect(result).to_contain_text('Synthetic mapping creation failed')
        expect(result).to_contain_text('APQC processes mapped: 2' if mapping_case == 'partial_errors'
                                       else 'APQC processes mapped: 0')
    elif mapping_case == 'success':
        expect(result).to_contain_text('APQC processes mapped: 1')
    else:
        expect(result).not_to_contain_text('APQC processes mapped:')
    if mapping_case != 'success':
        expect(result).not_to_contain_text('Auto-mapping completed')
    page.keyboard.press('Escape')
    expect(result).to_be_visible()
    assert len(state['posts']) == len(state['maps']) == 1
    acknowledge_import(page)
    expect(page.get_by_role('button', name='Open Import', exact=True)).to_be_visible()
    assert len(state['posts']) == len(state['maps']) == 1
