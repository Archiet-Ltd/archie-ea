"""Browser regressions for the active application import-history template.

Actual template, active scripts, Alpine and CSS; loopback HTTP supplies explicit
history/export/rollback responses. No database or real import mutations.
"""
import json
import csv
import io
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.parametrize('history_browser', ['chromium', 'firefox', 'webkit'], indirect=True)
ROWS = [
    {'id': 701, 'file_name': 'January completed fixture', 'import_source': 'csv',
     'status': 'completed', 'imported_at': '2026-01-10T12:00:00Z',
     'total_records': 4, 'records_created': 3, 'records_updated': 1,
     'records_skipped': 0, 'records_failed': 0, 'imported_by_name': 'Fixture owner',
     'errors': [], 'can_rollback': False, 'rollback_unavailable_reason': 'No recorded created applications.'},
    {'id': 702, 'file_name': 'February failed fixture', 'import_source': 'csv',
     'status': 'failed', 'imported_at': '2026-02-20T12:00:00Z',
     'total_records': 3, 'records_created': 1, 'records_updated': 0,
     'records_skipped': 0, 'records_failed': 2, 'imported_by_name': 'Fixture colleague',
     'errors': ['Fixture row rejected'], 'can_rollback': False,
     'rollback_unavailable_reason': 'No recorded created applications.'},
]


def history_html():
    base = '<!doctype html><html><head><meta charset="utf-8">'
    base += '<meta name="csrf-token" content="test-token">'
    for asset in ['css/shadcn_tokens.css', 'css/tailwind-output.css', 'css/app.css']:
        base += f'<link rel="stylesheet" href="/static/{asset}">'
    for asset in ['vendor/purify.min.js', 'vendor/lucide.min.js',
                  'js/bundles/core-admin.js', 'js/ui/modal.js']:
        base += f'<script src="/static/{asset}"></script>'
    for asset in ['vendor/alpine-focus.min.js', 'vendor/alpine-intersect.min.js',
                  'vendor/alpine-collapse.min.js', 'js/csp/csp-evaluator.js',
                  'js/csp/alpine-csp-adapter.js', 'vendor/alpine.min.js']:
        base += f'<script defer src="/static/{asset}"></script>'
    base += ('{% block extra_css %}{% endblock %}</head><body><main x-data>'
             '{% block content %}{% endblock %}</main>'
             '{% block extra_js %}{% endblock %}</body></html>')
    env = Environment(loader=ChoiceLoader([
        DictLoader({'layouts/admin_base.html': base}),
        FileSystemLoader(ROOT / 'app/templates')]), autoescape=True)
    return env.get_template('dashboard/import_history.html').render(
        url_for=lambda endpoint, **kw: '/static/' + kw['filename'] if endpoint == 'static'
        else '/dashboard/applications/import-history')


def wait_for_flag(page, flag, seconds=20):
    """Wait for a fixture-side ``Event`` while the browser keeps running.

    ``Event.wait`` blocks the thread that dispatches Playwright's own callbacks, and
    a fixed three seconds is less than a cold Firefox needs to boot Alpine and send
    its first request on a loaded runner. Polling through ``wait_for_timeout`` keeps
    the page serviced and leaves a deadline wide enough to mean "never happened".
    """
    deadline = time.monotonic() + seconds
    while not flag.is_set():
        if time.monotonic() > deadline:
            return False
        page.wait_for_timeout(25)
    return True


@pytest.fixture(scope='module')
def history_browser(request):
    with sync_playwright() as pw:
        browser = getattr(pw, request.param).launch()
        yield browser
        browser.close()


@pytest.fixture
def history_page(history_browser):
    contexts, servers, errors, unexpected = [], [], [], []
    closing = []

    def start(*, delayed=False):
        release = Event()
        if not delayed:
            release.set()
        state = {'rows': list(ROWS), 'status': 200, 'requests': [], 'started': Event(),
                 'plans': [], 'payload': None, 'writes': [], 'rollback_status': 200,
                 'rollback_success': True}
        document = history_html()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
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
                parsed = urlsplit(self.path)
                if parsed.path == '/':
                    return self.respond(document, 'text/html')
                if parsed.path.startswith('/static/'):
                    target = (ROOT / 'app' / parsed.path[1:]).resolve()
                    if not target.is_relative_to((ROOT / 'app/static').resolve()):
                        return self.respond({}, status=400)
                    return self.respond(target.read_bytes(),
                                        mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
                if parsed.path == '/dashboard/applications/import-history':
                    state['requests'].append(parse_qs(parsed.query))
                    state['started'].set()
                    plan = state['plans'].pop(0) if state['plans'] else {}
                    if plan.get('started') is not None:
                        plan['started'].set()
                    rows = list(plan.get('rows', state['rows']))
                    status = plan.get('status', state['status'])
                    gate = plan.get('release', release)
                    if not gate.wait(15):
                        return self.respond({'success': False, 'error': 'Fixture release timeout'}, status=500)
                    if status != 200:
                        return self.respond({'success': False, 'error': 'Fixture history outage'},
                                            status=status)
                    filters = parse_qs(parsed.query)
                    if filters.get('status'):
                        rows = [row for row in rows if row['status'] == filters['status'][0]]
                    if filters.get('date_from'):
                        rows = [row for row in rows if row['imported_at'][:10] >= filters['date_from'][0]]
                    if filters.get('date_to'):
                        rows = [row for row in rows if row['imported_at'][:10] <= filters['date_to'][0]]
                    if filters.get('format') == ['csv']:
                        if 'csv_body' in state:
                            return self.respond(state['csv_body'], state['csv_content_type'])
                        output = io.StringIO()
                        writer = csv.writer(output)
                        writer.writerow(['Import ID', 'File name', 'Imported at (UTC)', 'Imported by', 'Source',
                                         'Status', 'Total', 'Created', 'Updated', 'Skipped', 'Failed'])
                        writer.writerows([[row['id'], row['file_name'], row['imported_at'], row['imported_by_name'],
                            row['import_source'], row['status'], row['total_records'], row['records_created'],
                            row['records_updated'], row['records_skipped'], row['records_failed']] for row in rows])
                        return self.respond(output.getvalue(), 'text/csv')
                    if state['payload'] is not None:
                        return self.respond(state['payload'])
                    total = len(rows)
                    number = int(filters.get('page', ['1'])[0])
                    return self.respond({'success': True, 'history': rows[(number - 1) * 20:number * 20],
                                         'total': total, 'page': number, 'per_page': 20,
                                         'pages': (total + 19) // 20})
                if parsed.path == '/favicon.ico':
                    return self.respond(b'', 'image/x-icon', 204)
                if parsed.path == '/account/session/keepalive':
                    return self.respond({'success': True})
                unexpected.append(self.path)
                self.respond({}, status=404)

            def do_POST(self):
                if self.path == '/applications/rollback-import/701':
                    state['writes'].append(self.path)
                    self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    if state['rollback_status'] != 200 or not state['rollback_success']:
                        return self.respond({'success': False, 'error': 'Fixture rollback refused'},
                                            status=state['rollback_status'])
                    state['rows'] = [dict(row, status='rolled_back', can_rollback=False,
                                         rollback_reason='This import has already been rolled back.')
                                     if row['id'] == 701 else row for row in state['rows']]
                    return self.respond({'success': True, 'deleted': {'applications': 3}})
                if self.path == '/account/session/keepalive':
                    self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    return self.respond({'success': True})
                unexpected.append(self.path)
                self.respond({}, status=405)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread, release))
        context = history_browser.new_context(viewport={'width': 1440, 'height': 1000})
        contexts.append(context)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        # A request still in flight when the context closes is reported as failed by
        # Firefox and WebKit; that is teardown, not a page defect.
        page.on('requestfailed', lambda request: None if closing else errors.append(request.failure))
        origin = f'http://127.0.0.1:{server.server_port}'
        page.on('request', lambda request: unexpected.append(request.url)
                if not request.url.startswith(origin + '/') else None)
        page.goto(origin + '/', wait_until='domcontentloaded')
        assert wait_for_flag(page, state['started']), 'the history request was never sent'
        return page, state, release

    yield start
    closing.append(True)
    for _, _, release in servers:
        release.set()
    for context in contexts:
        context.close()
    for server, thread, _ in servers:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    assert unexpected == []


@pytest.mark.parametrize('filters', [
    {'status': 'failed'}, {'date_from': '2026-02-01'}, {'date_to': '2026-01-31'},
])
def test_apply_sends_filters_and_changes_visible_records(history_page, filters):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    page.get_by_label('Date from', exact=True).fill('')
    page.get_by_label('Date to', exact=True).fill('')
    if 'status' in filters:
        page.get_by_label('Status Filter', exact=True).select_option(filters['status'])
    if 'date_from' in filters:
        page.get_by_label('Date from', exact=True).fill(filters['date_from'])
    if 'date_to' in filters:
        page.get_by_label('Date to', exact=True).fill(filters['date_to'])
    with page.expect_response(lambda response: '/dashboard/applications/import-history' in response.url):
        page.get_by_role('button', name='Apply filters to history', exact=True).click()
    assert state['requests'][-1] == {**{key: [value] for key, value in filters.items()},
                                    'page': ['1'], 'per_page': ['20']}
    expect(page.locator('.import-item')).to_have_count(1)


def test_refresh_preserves_current_filters_and_loads_new_data(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    page.get_by_label('Status Filter', exact=True).select_option('failed')
    page.get_by_label('Date from', exact=True).fill('2026-02-01')
    page.get_by_label('Date to', exact=True).fill('2026-02-28')
    state['rows'] = [ROWS[0], ROWS[1], dict(ROWS[1], id=703)]
    with page.expect_response(lambda response: '/dashboard/applications/import-history' in response.url):
        page.get_by_role('button', name='Refresh', exact=True).click()
    assert state['requests'][-1] == {'status': ['failed'], 'date_from': ['2026-02-01'],
                                    'date_to': ['2026-02-28'], 'page': ['1'], 'per_page': ['20']}
    expect(page.locator('.import-item')).to_have_count(2)


def test_actual_api_record_names_and_dates_are_visible(history_page):
    page, _, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    expect(page.locator('#import-history-list')).to_contain_text('January completed fixture')
    expect(page.locator('#import-history-list')).not_to_contain_text('Invalid Date')


def test_refresh_distinguishes_empty_and_error(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['rows'] = []
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('#empty-state')).to_be_visible()
    expect(page.locator('#error-state')).to_be_hidden()
    expect(page.locator('#total-imports')).to_have_text('0')
    state['status'] = 500
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('#error-state')).to_be_visible()
    expect(page.locator('#empty-state')).to_be_hidden()
    expect(page.locator('#total-imports')).to_have_text('—')


def test_loading_remains_visible_until_initial_request_finishes(history_page):
    page, state, release = history_page(delayed=True)
    try:
        assert state['requests'] == [{'page': ['1'], 'per_page': ['20']}]
        expect(page.locator('#loading')).to_be_visible()
    finally:
        release.set()
        expect(page.locator('.import-item')).to_have_count(2)


@pytest.mark.parametrize('older_status,newer_status', [(200, 200), (500, 200), (200, 500)])
def test_newest_request_owns_results_errors_and_loading(history_page, older_status, newer_status):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    older_release = Event()
    older_started = Event()
    state['plans'] = [{'rows': [ROWS[0]], 'status': older_status, 'release': older_release, 'started': older_started},
                      {'rows': [ROWS[1]], 'status': newer_status}]
    page.get_by_label('Status Filter', exact=True).select_option('completed')
    page.get_by_role('button', name='Apply filters to history', exact=True).click()
    assert wait_for_flag(page, older_started), 'the superseded request was never sent'
    expect(page.locator('#loading')).to_be_visible()
    page.get_by_label('Status Filter', exact=True).select_option('failed')
    with page.expect_response(lambda response: '/dashboard/applications/import-history' in response.url):
        page.get_by_role('button', name='Apply filters to history', exact=True).click()
    if newer_status == 200:
        expect(page.locator('.import-item')).to_have_count(1)
        expect(page.locator('#import-history-list')).to_contain_text('February failed fixture')
    else:
        expect(page.locator('#error-state')).to_be_visible()
    try:
        with page.expect_response(lambda response: '/dashboard/applications/import-history' in response.url) as old:
            older_release.set()
        old.value.finished()
        # Give the completed response's microtasks and actual renderer a paint;
        # no production function or request is replaced for this race.
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        expect(page.locator('#loading')).to_be_hidden()
        if newer_status == 200:
            expect(page.locator('#error-state')).to_be_hidden()
            expect(page.locator('#import-history-list')).to_contain_text('February failed fixture')
            expect(page.locator('#import-history-list')).not_to_contain_text('January completed fixture')
        else:
            expect(page.locator('#error-state')).to_be_visible()
            expect(page.locator('.import-item')).to_have_count(0)
    finally:
        older_release.set()


@pytest.mark.parametrize('payload', [{'success': True}, {'success': True, 'history': None},
                                    {'success': False, 'error': 'Fixture refusal'},
                                    {'success': True, 'history': [None], 'total': 1, 'pages': 1, 'page': 1},
                                    {'success': True, 'history': [{'id': 1, 'status': {}}], 'total': 1, 'pages': 1, 'page': 1}])
def test_malformed_response_is_not_reported_as_empty(history_page, payload):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['payload'] = payload
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('#error-state')).to_be_visible()
    expect(page.locator('#empty-state')).to_be_hidden()
    expect(page.locator('#total-imports')).to_have_text('—')


def test_inverted_dates_report_error_without_request_and_clear_recovers(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    before = len(state['requests'])
    page.get_by_label('Date from', exact=True).fill('2026-03-01')
    page.get_by_label('Date to', exact=True).fill('2026-02-01')
    page.get_by_role('button', name='Apply filters to history', exact=True).click()
    expect(page.locator('#error-state')).to_contain_text('Date from must not be after Date to')
    assert len(state['requests']) == before
    page.get_by_role('button', name='Clear Filters', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(2)
    expect(page.get_by_label('Date from', exact=True)).to_have_value('')
    expect(page.get_by_label('Date to', exact=True)).to_have_value('')


def test_details_and_null_fields_are_truthful_and_text_safe(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['rows'] = [dict(ROWS[0], file_name='<img src=x onerror="window.historyInjected=true">',
                         records_created=None, imported_at=None, errors=['<script>fixture</script>'])]
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(1)
    expect(page.locator('#total-created')).to_have_text('—')
    expect(page.locator('#import-history-list')).to_contain_text('<script>fixture</script>')
    page.get_by_role('button', name='View import details', exact=True).click()
    dialog = page.get_by_role('dialog', name='Import Details', exact=True)
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('<img src=x onerror="window.historyInjected=true">')
    assert page.evaluate('window.historyInjected') is None
    dialog.get_by_role('button', name='Close', exact=True).click()
    expect(dialog).to_be_hidden()


@pytest.mark.parametrize('close_action', ['close', 'escape'])
def test_details_dialog_focus_returns_to_ordinary_invoker(history_page, close_action):
    page, _, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    invoker = page.get_by_role('button', name='View import details', exact=True).first
    invoker.click()
    dialog = page.get_by_role('dialog', name='Import Details', exact=True)
    expect(dialog).to_be_visible()
    if close_action == 'close':
        dialog.get_by_role('button', name='Close', exact=True).click()
    else:
        page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    expect(invoker).to_be_focused()


def test_export_download_respects_selected_filters(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    page.get_by_label('Status Filter', exact=True).select_option('failed')
    with page.expect_download() as download:
        page.get_by_role('button', name='Export CSV', exact=True).click()
    assert download.value.suggested_filename == 'application-import-history.csv'
    assert download.value.path().read_text(encoding='utf-8-sig') == (
        'Import ID,File name,Imported at (UTC),Imported by,Source,Status,Total,Created,Updated,Skipped,Failed\n'
        '702,February failed fixture,2026-02-20T12:00:00Z,Fixture colleague,csv,failed,3,1,0,0,2\n')
    assert state['requests'][-1] == {'status': ['failed'], 'format': ['csv']}


@pytest.mark.parametrize('status', [400, 401, 403, 500])
def test_export_http_failure_is_visible_and_never_downloads(history_page, status):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    state['status'] = status
    page.get_by_role('button', name='Export CSV', exact=True).click()
    expect(page.locator('#error-state')).to_be_visible()
    expect(page.locator('#error-state')).to_contain_text('Fixture history outage')
    assert downloads == []


@pytest.mark.parametrize('body,content_type', [('<html>Sign in</html>', 'text/html'),
                                             ({'success': False}, 'application/json')])
def test_export_rejects_non_csv_success_response(history_page, body, content_type):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    state.update(csv_body=body, csv_content_type=content_type)
    page.get_by_role('button', name='Export CSV', exact=True).click()
    expect(page.locator('#error-state')).to_contain_text('not a valid history CSV')
    assert downloads == []


def test_pagination_uses_true_total_and_page_specific_statistics(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['rows'] = [dict(ROWS[0], id=index, file_name=f'Paged fixture {index}') for index in range(25)]
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(20)
    expect(page.locator('#total-imports')).to_have_text('25')
    expect(page.locator('#total-created')).to_have_text('60')
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(5)
    expect(page.locator('#total-created')).to_have_text('15')
    expect(page.get_by_role('navigation', name='Import history pages')).to_contain_text('Page 2 of 2')
    expect(page.get_by_role('button', name='Next', exact=True)).to_be_disabled()


def test_refresh_recovers_when_current_last_page_no_longer_exists(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['rows'] = [dict(ROWS[0], id=index, file_name=f'Paged fixture {index}') for index in range(25)]
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(20)
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(5)
    state['rows'] = list(ROWS)
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(2)
    expect(page.get_by_role('navigation', name='Import history pages')).to_contain_text('Page 1 of 1')


def test_page_recovery_keeps_request_filters_when_draft_controls_change(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['rows'] = [dict(ROWS[0], id=index, file_name=f'Paged fixture {index}') for index in range(25)]
    page.get_by_label('Status Filter').select_option('completed')
    page.get_by_role('button', name='Apply filters to history').click()
    expect(page.locator('.import-item')).to_have_count(20)
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(5)
    release, started = Event(), Event()
    state['rows'] = list(ROWS)
    state['plans'] = [{'release': release, 'started': started}]
    before = len(state['requests'])
    page.get_by_role('button', name='Refresh', exact=True).click()
    assert wait_for_flag(page, started), 'the refresh request was never sent'
    page.get_by_label('Status Filter').select_option('failed')
    release.set()
    expect(page.locator('.import-item')).to_have_count(1)
    expect(page.locator('.import-item')).to_contain_text('January completed fixture')
    assert [query['status'] for query in state['requests'][before:]] == [['completed'], ['completed']]
    assert [query['page'] for query in state['requests'][before:]] == [['2'], ['1']]


def test_page_recovery_stops_after_one_retry_and_displays_failure(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['payload'] = {'success': True, 'history': [], 'total': 2,
                        'page': 2, 'pages': 1, 'per_page': 20}
    before = len(state['requests'])
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('#error-state')).to_contain_text('Could not load import history')
    expect(page.locator('#loading')).to_be_hidden()
    expect(page.locator('#empty-state')).to_be_hidden()
    expect(page.locator('#total-imports')).to_have_text('—')
    assert len(state['requests']) - before == 2


def rollback_ready(history_page):
    page, state, _ = history_page()
    expect(page.locator('.import-item')).to_have_count(2)
    state['rows'] = [dict(ROWS[0], can_rollback=True, rollback_created_count=3, rollback_reason=None)]
    page.get_by_role('button', name='Refresh', exact=True).click()
    expect(page.locator('.import-item')).to_have_count(1)
    return page, state


@pytest.mark.parametrize('close_action', ['cancel', 'escape', 'close'])
def test_rollback_confirmation_cancel_returns_focus_and_never_writes(history_page, close_action):
    page, state = rollback_ready(history_page)
    invoker = page.get_by_role('button', name='Rollback import', exact=True)
    invoker.click()
    dialog = page.get_by_role('dialog', name='Confirm Import Rollback', exact=True)
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('3 recorded created applications')
    expect(dialog).to_contain_text('does not restore previous values')
    if close_action == 'escape':
        page.keyboard.press('Escape')
    else:
        dialog.get_by_role('button', name='Cancel' if close_action == 'cancel' else 'Close', exact=True).click()
    expect(dialog).to_be_hidden()
    expect(invoker).to_be_focused()
    assert state['writes'] == []


def test_confirmed_rollback_writes_exact_application_history_id_once_and_reloads(history_page):
    page, state = rollback_ready(history_page)
    page.get_by_role('button', name='Rollback import', exact=True).click()
    dialog = page.get_by_role('dialog', name='Confirm Import Rollback', exact=True)
    dialog.get_by_role('button', name='Confirm rollback', exact=True).dblclick()
    expect(dialog).to_be_hidden()
    expect(page.locator('#import-history-list')).to_contain_text('Rolled back')
    assert state['writes'] == ['/applications/rollback-import/701']
    page.reload()
    expect(page.locator('#import-history-list')).to_contain_text('Rolled back')
    expect(page.get_by_role('button', name='Rollback import', exact=True)).to_be_disabled()


@pytest.mark.parametrize('status,success', [(400, False), (403, False), (404, False), (500, False), (200, False)])
def test_failed_rollback_retains_visible_error_and_record(history_page, status, success):
    page, state = rollback_ready(history_page)
    state['rollback_status'] = status
    state['rollback_success'] = success
    page.get_by_role('button', name='Rollback import', exact=True).click()
    dialog = page.get_by_role('dialog', name='Confirm Import Rollback', exact=True)
    dialog.get_by_role('button', name='Confirm rollback', exact=True).click()
    expect(dialog).to_be_visible()
    expect(dialog.locator('[role=alert]')).to_contain_text('Fixture rollback refused')
    expect(dialog.get_by_role('button', name='Confirm rollback', exact=True)).to_be_enabled()
    assert state['writes'] == ['/applications/rollback-import/701']
    assert state['rows'][0]['status'] == 'completed'
