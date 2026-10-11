"""Computed-style contracts for real card fragments, not authenticated journeys.

The templates and CSS/Alpine assets are unchanged inputs. Only the surrounding
layout and recorded state are fixtures. No app/database/provider runs, and no
production Undo/approval handler is replaced or claimed to have been exercised.
Reintroducing a colored edge, corner strip, tinted card fill or lost status/action
markup must fail these controls in both themes and every browser engine.
"""
import html
import json
import mimetypes
from pathlib import Path

import pytest
from jinja2 import Environment
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def fragment(path, start, end):
    source = (ROOT / 'app/templates' / path).read_text(encoding='utf-8')
    assert source.count(start) == 1, f'Ambiguous template fragment: {path}: {start}'
    return source.split(start, 1)[1].split(end, 1)[0]


@pytest.fixture(scope='module', params=['chromium', 'firefox', 'webkit'])
def browser(request):
    with sync_playwright() as pw:
        instance = getattr(pw, request.param).launch()
        yield instance
        instance.close()


@pytest.fixture(params=['light', 'dark'])
def render_card(browser, request):
    pages = []
    errors = []
    unexpected = []

    def render(markup, state=None, **template_data):
        markup = Environment(autoescape=True).from_string(markup).render(
            url_for=lambda endpoint, **kw: '/solutions/1/edit', **template_data)
        state_attr = html.escape(json.dumps(state or {}), quote=True)
        document = f'''<!doctype html><html class="{request.param}"><head>
          <meta charset="utf-8">
          <link rel="stylesheet" href="/static/css/shadcn_tokens.css">
          <link rel="stylesheet" href="/static/css/tailwind-output.css">
          <link rel="stylesheet" href="/static/css/app.css">
          <script src="/static/vendor/lucide.min.js"></script>
          <script defer src="/static/vendor/alpine.min.js"></script>
          </head><body class="bg-background p-6">
          <div id="reference" class="border border-border bg-card rounded-lg"></div>
          <div id="fixture" x-data="{state_attr}">{markup}</div></body></html>'''
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        pages.append(page)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)

        def respond(route):
            from urllib.parse import urlsplit
            path = urlsplit(route.request.url).path
            if path == '/':
                route.fulfill(body=document, content_type='text/html')
            elif path.startswith('/static/'):
                target = (ROOT / 'app' / path.lstrip('/')).resolve()
                assert target.is_relative_to((ROOT / 'app/static').resolve())
                route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            else:
                unexpected.append(path)
                route.abort()

        page.route('**/*', respond)
        page.goto('http://fixture.invalid/', wait_until='networkidle')
        page.evaluate('lucide.createIcons()')
        return page

    yield render
    for page in pages:
        page.close()
    assert errors == [], errors
    assert unexpected == [], unexpected


def assert_neutral_card(page, card):
    expect(card).to_be_visible()
    expected = page.locator('#reference').evaluate('e => {const s=getComputedStyle(e); return {border:s.borderTopColor, background:s.backgroundColor};}')
    actual = card.evaluate('''e => {
        const s=getComputedStyle(e);
        return {widths:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth],
          colors:[s.borderTopColor,s.borderRightColor,s.borderBottomColor,s.borderLeftColor],
          radii:[s.borderTopLeftRadius,s.borderTopRightRadius,s.borderBottomRightRadius,s.borderBottomLeftRadius],
          background:s.backgroundColor};
    }''')
    assert actual == {'widths': ['1px'] * 4, 'colors': [expected['border']] * 4,
                      'radii': ['8px'] * 4, 'background': expected['background']}, actual


def test_journey_intro_has_no_decorative_edge_or_corner(render_card):
    source = (ROOT / 'app/templates/architecture_assistant/architecture_journey_hub.html').read_text(encoding='utf-8')
    # The first section is the introductory card, before the journey form.
    markup = '<section' + source.split('<section', 1)[1].split('</section>', 1)[0] + '</section>'
    page = render_card(markup)
    card = page.locator('#fixture > section')
    assert_neutral_card(page, card)
    expect(card.locator(':scope > [aria-hidden="true"]')).to_have_count(0)
    expect(card.get_by_role('heading', level=2)).to_be_visible()
    expect(card.get_by_role('list', name='Architecture journey stages')).to_be_visible()


def test_copilot_card_retains_guidance_and_chat_action(render_card):
    markup = fragment('architecture_assistant/journey_v3.html',
        '{# ── Co-Pilot Banner (full-width, inline) — hidden on locked codegen/deploy steps (7,8) ── #}',
        '{# ── Error Banner ── #}')
    page = render_card(markup, {'currentStep': 1, 'copilotMessage': 'Recorded guidance',
        'loading': False, 'capabilities': [], 'acceptedCapabilities': [],
        'architectureResult': None, 'domainsPopulated': False, 'domainBlockers': [],
        '_propSaveIndicator': False})
    card = page.locator('#fixture > div')
    assert_neutral_card(page, card)
    expect(card).to_contain_text('Recorded guidance')
    expect(card.get_by_role('button', name='Ask Entelim')).to_be_visible()


def test_enriched_brief_retains_status_and_start_over(render_card):
    markup = fragment('architecture_assistant/journey_v2_steps/_step1_clarify.html',
        '{# ── Enriched brief preview ───── #}', '{# ── Sub-step navigation (context) ── #}')
    page = render_card(markup, {'clarifyPhase': 'enriched', 'enrichedBrief': 'Recorded enriched brief'})
    card = page.locator('#fixture > div')
    assert_neutral_card(page, card)
    expect(card).to_contain_text('Recorded enriched brief')
    expect(card).to_contain_text('AI-enhanced')
    expect(card.get_by_role('button', name='Start over')).to_be_visible()


@pytest.mark.parametrize('description', ['Recorded executive summary', ''])
def test_executive_summary_populated_and_empty_are_neutral(render_card, description):
    markup = fragment('solutions/detail.html', '{# Executive Summary callout #}',
                      '{# Strategic Context: stat cards + inline list #}')
    page = render_card(markup, solution={'id': 1, 'description': description})
    card = page.locator('#fixture > div')
    assert_neutral_card(page, card)
    expect(card).to_contain_text(description or 'No executive summary yet')
    if not description:
        expect(card.get_by_role('link', name='add one')).to_have_attribute('href', '/solutions/1/edit')


def test_dynamic_action_cards_keep_status_and_undo_affordance(render_card):
    markup = fragment('solutions/blueprint.html', '{# Action cards #}', '{# Approval cards #}')
    page = render_card(markup, {'msg': {'role': 'actions', 'actions': [{
        'message': 'Recorded change', 'tool': 'fixture', 'undone': False,
        'undoExpired': False, 'result': {'id': 1}}]}})
    card = page.locator('#fixture [x-for] + div')
    assert_neutral_card(page, card)
    expect(card).to_contain_text('✓')
    expect(card).to_contain_text('Recorded change')
    expect(card.get_by_role('button', name='Undo', exact=True)).to_be_visible()
    # Change recorded state, not the production Undo operation.
    page.evaluate("Alpine.$data(document.querySelector('#fixture')).msg.actions[0].undone = true")
    expect(card).to_contain_text('Undone')
    expect(card.get_by_role('button', name='Undo', exact=True)).to_have_count(0)
    assert_neutral_card(page, card)


def test_dynamic_approval_cards_keep_summary_and_actions(render_card):
    markup = fragment('solutions/blueprint.html', '{# Approval cards #}', '{# Error #}')
    page = render_card(markup, {'msg': {'role': 'approvals', 'approvals': [
        {'summary': 'Recorded approval request', 'dismissed': False}]}})
    card = page.locator('#fixture [x-for] + div')
    assert_neutral_card(page, card)
    expect(card).to_contain_text('Recorded approval request')
    expect(card).to_contain_text('⏳')
    expect(card.get_by_role('button', name='Approve', exact=True)).to_be_visible()
    expect(card.get_by_role('button', name='Dismiss', exact=True)).to_be_visible()
    page.evaluate("Alpine.$data(document.querySelector('#fixture')).msg.approvals[0].dismissed = true")
    expect(card).to_be_hidden()


def test_dynamic_composer_gap_tile_retains_recorded_warning(render_card):
    # Include the real conditional/list, stopping before surrounding details.
    markup = fragment('archimate/partials/_composer_overlays.html', '{# Gaps #}', '</details>')
    markup = markup.rsplit('</div>', 1)[0]
    page = render_card(markup, {'generateGaps': ['Recorded missing architecture evidence']})
    card = page.locator('#fixture [x-text="gap"]')
    assert_neutral_card(page, card)
    expect(card).to_have_text('Recorded missing architecture evidence')
    expect(page.locator('[data-lucide="alert-circle"]')).to_be_visible()
    page.evaluate("Alpine.$data(document.querySelector('#fixture')).generateGaps = []")
    expect(card).to_have_count(0)
