"""Application roadmap modal using the real template and shipped browser assets.

The surrounding authenticated layout and read-only JSON APIs are test boundaries;
roadmap HTML/Alpine code, styles, Platform modal/core and CSP evaluator are real.
No Flask/DB or production writes. Normal browser clicks only.
"""
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://roadmap.test'
MODAL = '#roadmap-work-package-modal'
STYLES = ['css/shadcn_tokens.css', 'css/tailwind-output.css', 'css/app.css',
          'css/accessibility.css', 'styles/shadcn-sidebar.css',
          'styles/shadcn-components.css', 'styles/shadcn-cards.css']
SCRIPTS = ['vendor/lucide.min.js', 'js/bundles/core-admin.js', 'js/ui/modal.js',
           'vendor/alpine-focus.min.js', 'vendor/alpine-intersect.min.js',
           'vendor/alpine-collapse.min.js', 'js/csp/csp-evaluator.js',
           'js/csp/alpine-csp-adapter.js', 'vendor/alpine.min.js']


def page_html():
    head = ''.join('<link rel="stylesheet" href="/static/' + path + '">' for path in STYLES)
    head += ''.join('<script defer src="/static/' + path + '"></script>' for path in SCRIPTS)
    base = ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="csrf-token" content="test-token">'
            '<style>[x-cloak]{display:none!important}</style>' + head + '</head><body>'
            '{% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>')
    env = Environment(loader=ChoiceLoader([
        DictLoader({'layouts/admin_base.html': base}), FileSystemLoader(ROOT / 'app/templates')
    ]), autoescape=True)
    return env.get_template('applications/roadmap.html').render(
        application={'id': 32, 'name': 'Payments'},
        work_packages=[{'id': 7, 'name': 'Existing migration', 'description': 'Move application',
                        'transformation_type': 'Migration', 'status': 'planned', 'priority': 'high',
                        'start_date': '2026-10-01', 'target_date': '2027-06-01'}],
        target_arch_elements=[], csrf_token=lambda: 'test-token',
        url_for=lambda endpoint, **kwargs: '/applications/32')


@pytest.fixture(scope='module', params=['chromium', 'firefox', 'webkit'])
def browser(request):
    with sync_playwright() as pw:
        instance = getattr(pw, request.param).launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    p = context.new_page()
    errors = []
    p.on('pageerror', lambda error: errors.append(str(error)))
    p.route(BASE + '/static/**', lambda route: route.fulfill(
        path=str(ROOT / 'app' / route.request.url[len(BASE) + 1:])))
    p.route(BASE + '/capability-map/api/roadmap/plateaus', lambda route: route.fulfill(
        content_type='application/json', body='{"plateaus":[]}'))
    p.route(BASE + '/api/capabilities/grouped', lambda route: route.fulfill(
        content_type='application/json', body='{"capabilities":{}}'))
    p.route(BASE + '/', lambda route: route.fulfill(content_type='text/html', body=page_html()))
    p.goto(BASE + '/')
    p.wait_for_function('window.Alpine && window.Platform && window.Platform.modal')
    yield p
    context.close()
    assert errors == [], 'Browser runtime errors: ' + '; '.join(errors)


def test_first_add_click_opens_then_cancel_allows_reopen(page):
    """A closed overlay must never intercept the first Add Work Package click."""
    trigger = page.get_by_role('button', name='Add Work Package', exact=True)
    trigger.click(timeout=3000)
    modal = page.locator(MODAL)
    expect(modal).to_be_visible()
    expect(modal).to_have_attribute('aria-hidden', 'false')
    expect(modal.get_by_role('heading', name='Create Work Package', exact=True)).to_be_visible()
    expect(modal.locator(':focus')).to_have_count(1)
    modal.get_by_role('button', name='Cancel', exact=True).click()
    expect(modal).to_be_hidden()
    trigger.click()
    expect(modal).to_be_visible()


def test_initial_and_dismissed_dialog_have_no_layout_or_pointer_surface(page):
    modal = page.locator(MODAL)
    expect(modal).to_be_hidden()
    expect(modal).to_have_css('display', 'none')
    trigger = page.get_by_role('button', name='Add Work Package', exact=True)
    trigger.click()
    expect(modal.locator(':focus')).to_have_count(1)
    page.keyboard.press('Escape')
    expect(modal).to_be_hidden()
    expect(modal).to_have_attribute('aria-hidden', 'true')
    # Focus containment remains modal-owned; after dismissal the page is operable.
    trigger.click()
    modal.get_by_role('button', name='Close', exact=True).click()
    expect(modal).to_be_hidden()
    trigger.click()
    page.locator(MODAL + ' [data-modal-backdrop]').click(position={'x': 2, 'y': 2})
    expect(modal).to_be_hidden()


def test_edit_opens_with_existing_values_and_keyboard_stays_in_dialog(page):
    # The Edit control lives in the List view; the Timeline bars open the
    # editor too, but the row controls follow the List layout.
    page.get_by_role('tab', name='List').click()
    row = page.get_by_role('row').filter(has_text='Existing migration')
    row.get_by_role('button', name='Edit', exact=True).click(timeout=3000)
    modal = page.locator(MODAL)
    expect(modal.get_by_role('heading', name='Edit Work Package', exact=True)).to_be_visible()
    expect(modal.get_by_label('Name', exact=True)).to_have_value('Existing migration')
    expect(modal.get_by_label('Transformation Type', exact=True)).to_have_value('Migration')
    expect(modal.locator(':focus')).to_have_count(1)
    # Shift-Tab from the first focusable element wraps to the final submit button.
    page.keyboard.press('Shift+Tab')
    expect(modal.get_by_role('button', name='Save Changes', exact=True)).to_be_focused()
    page.keyboard.press('Tab')
    expect(modal.get_by_role('button', name='Close', exact=True)).to_be_focused()
    page.keyboard.press('Escape')
    expect(modal).to_be_hidden()
