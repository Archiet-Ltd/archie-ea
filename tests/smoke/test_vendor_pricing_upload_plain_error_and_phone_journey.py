"""platform_admin: a failed document extraction shows one plain sentence and
never the exception text; the review card reads whole at 390 wide, light and dark."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_archetype_journeys import _login
from .test_vendor_pricing_upload_journey import URL, _page_template_csv

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ADDRESS = '10.20.30.40:5432'
KEYLIKE = 'sk-live-AbCdEf0123456789XyZ'
PLAIN = "We couldn't read this document. Try a CSV or XLSX file instead."

OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const scroller = (el, card) => { for (let p = el.parentElement; p && p !== card; p = p.parentElement) {
    const o = getComputedStyle(p).overflowX;
    if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
  const out = [];
  for (const card of document.querySelectorAll('main .rounded-lg.border')) {
    const edge = Math.min(card.getBoundingClientRect().right, vw);
    for (const el of card.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || scroller(el, card)) continue;
      if (r.right > edge + 1) out.push(el.tagName + ' ' + (el.textContent || '').trim().slice(0, 30) + ' ' + Math.round(r.right) + '>' + Math.round(edge));
    }
  }
  const f = document.querySelector('input[type=file]').getBoundingClientRect();
  if (f.width < 250) out.push('file input too narrow ' + Math.round(f.width));
  return out;
}"""


@pytest.fixture(scope='module')
def failing_model():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.dumps({'error': {'message': 'connect to %s failed, key %s' % (ADDRESS, KEYLIKE)}}).encode()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    srv.daemon_threads = True
    Thread(target=srv.serve_forever, daemon=True).start()
    yield 'http://127.0.0.1:%d/v1' % srv.server_port
    srv.shutdown()


@pytest.fixture(scope='module')
def failing_server(request, app, failing_model, seeded):
    env = {}
    for prov in ('ANTHROPIC', 'AZURE', 'CLAUDE', 'GEMINI', 'GOOGLE', 'HUGGINGFACE', 'NVIDIA', 'OPENAI', 'OPENROUTER'):
        env[prov + '_API_KEY'] = ''
    env.update(LLM_API_KEY='', AZURE_OPENAI_API_KEY='', OPENAI_API_KEY='ci-dummy-token',
               OPENAI_BASE_URL=failing_model, NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    return boot_live_server(request, None, app, extra_env=env)


def test_failed_extraction_shows_plain_sentence_never_exception_text(browser, failing_server, seeded):
    page = browser.new_page()
    try:
        _login(page, failing_server, seeded['emails']['platform_admin'])
        page.goto(failing_server + URL, timeout=PAGE_TIMEOUT)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'contract.txt', 'mimeType': 'text/plain',
            'buffer': b'Acme Cloud Suite, Enterprise tier, annual cost 120000 USD, 100 users.'}])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text(PLAIN)).to_be_visible(timeout=PAGE_TIMEOUT)
        body = page.locator('body').inner_text()
        assert ADDRESS not in body and '10.20.30.40' not in body
        assert KEYLIKE not in body and 'sk-live' not in body
        expect(page.locator('input[name="file"]')).to_be_visible()
    finally:
        page.close()


@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_review_card_fits_a_phone(browser, live_server, seeded, scheme):
    page = browser.new_page(viewport={'width': 390, 'height': 844}, color_scheme=scheme)
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        csv_text = _page_template_csv(page)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'pricing.csv', 'mimeType': 'text/csv', 'buffer': csv_text.encode()}])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text('2 items ready for review')).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role('button', name=re.compile('Confirm All'))).to_be_visible()
        assert page.evaluate(OVERFLOW_JS) == []
    finally:
        page.close()
