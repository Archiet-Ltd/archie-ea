"""Actual viewer template/JS; synthetic shell and read-only HTTP boundaries."""
import mimetypes
import json
from pathlib import Path

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
import pytest
from playwright.sync_api import expect, sync_playwright

from app.basecoat_kit import register_basecoat_helpers

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://model-viewer.test'


@pytest.mark.parametrize('engine', ['chromium', 'firefox', 'webkit'])
@pytest.mark.parametrize('scenario', ['empty_back', 'neutral_cards'])
def test_model_viewer_navigation_and_card_contract(engine, scenario):
    shell = '''<!doctype html><html><head>
    <link rel="stylesheet" href="/static/css/shadcn_tokens.css">
    <link rel="stylesheet" href="/static/css/tailwind-output.css">
    <script src="/static/vendor/lucide.min.js"></script>
    <script src="/static/vendor/purify.min.js"></script>
    <script src="/static/js/bundles/core-admin.js"></script>
    {% block extra_css %}{% endblock %}</head><body>
    {% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>'''
    env = Environment(loader=ChoiceLoader([
        DictLoader({'layouts/admin_base.html': shell}),
        FileSystemLoader(ROOT / 'app/templates')]), autoescape=True)
    register_basecoat_helpers(env)
    document = env.get_template('architecture_assistant/archimate_model_viewer.html').render()
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch()
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def respond(route):
                path = route.request.url.removeprefix(BASE).split('?')[0]
                if path.startswith('/static/'):
                    target = ROOT / 'app' / path.lstrip('/')
                    route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
                elif path == '/api/archimate/viewpoints':
                    route.fulfill(json={})
                elif path == '/account/session/keepalive':
                    # Synthetic session boundary; this test does not qualify auth.
                    route.fulfill(json={'success': True})
                elif path == '/architecture-assistant/':
                    route.fulfill(body='<h1>Architecture Assistant</h1>', content_type='text/html')
                elif path == '/viewer':
                    route.fulfill(body=document, content_type='text/html')
                else:
                    route.abort()
                    errors.append('Unexpected request: ' + path)

            page.route('**/*', respond)
            if scenario == 'neutral_cards':
                layers = ['business', 'application', 'technology', 'motivation', 'strategy']
                model = {'id': 'fixture', 'name': 'Synthetic card fixture',
                         'elements': [{'id': layer, 'name': layer + ' fixture', 'type': 'Fixture',
                                       'layer': layer} for layer in layers],
                         'relationships': [], 'viewpoints': []}
                # Cache is an explicit fixture input, not proof of generation or
                # database persistence. Rendering and layer clicks are real.
                page.add_init_script('localStorage.setItem("archimate_generated_models", ' +
                                     json.dumps(json.dumps({'fixture': model})) + ');')
                page.goto(BASE + '/viewer?model_id=fixture')
                for layer in layers:
                    page.get_by_role('button', name=f'Filter {layer.title()} Layer elements', exact=True).click()
                    card = page.locator('#elements-container .model-element')
                    expect(card).to_have_count(1)
                    expect(card).to_contain_text(layer + ' fixture')
                    styles = card.evaluate('''el => {
                        const s = getComputedStyle(el);
                        return ['Top', 'Right', 'Bottom', 'Left'].map(side =>
                            [s['border' + side + 'Width'], s['border' + side + 'Color']]);
                    }''')
                    assert styles == [styles[0]] * 4, (layer, styles)
                    assert styles[0][0] == '1px', (layer, styles)
                assert errors == [], errors
                return
            page.goto(BASE + '/viewer')
            assert errors == [], errors
            expect(page.get_by_text('No architecture assistant data found. Please run the assistant first.', exact=True)).to_be_visible()
            page.get_by_role('button', name='Back to Architecture Assistant', exact=True).click()
            expect(page).to_have_url(BASE + '/architecture-assistant/')
            expect(page.get_by_role('heading', name='Architecture Assistant', exact=True)).to_be_visible()
            assert errors == []
        finally:
            browser.close()
