"""Escape closes only the top layer: a modal stacked over an open drawer closes alone, then the drawer."""
import pytest
from playwright.sync_api import expect

from tests.test_ai_model_selector_display import browser, launch  # noqa: F401
from tests.chat_opening_fixture import open_chat_options

pytestmark = pytest.mark.parametrize('browser', ['chromium'], indirect=True)
MODEL = {'model': 'fixture-0', 'display_name': 'Fixture 0', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None}


def test_one_escape_closes_only_the_modal_stacked_over_a_drawer(launch):
    page, _ = launch([MODEL])
    page.keyboard.press('Escape')
    open_chat_options(page)
    sheet = page.get_by_role('dialog', name='Chat options')
    page.evaluate("""() => {
        const el = document.createElement('div');
        el.id = 'stacked-test-modal'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Stacked test');
        el.tabIndex = -1; el.textContent = 'stacked';
        document.body.appendChild(el);
        Platform.modal.register('stacked-test-modal'); Platform.modal.open('stacked-test-modal');
    }""")
    assert page.evaluate("Platform.modal.stack().slice(-2)") == ['drawer-chat-options', 'stacked-test-modal']
    page.keyboard.press('Escape')
    page.wait_for_timeout(500)
    assert page.evaluate("Platform.modal.isOpen('stacked-test-modal')") is False
    expect(sheet).to_be_visible()
    assert page.evaluate("Platform.modal.isOpen('drawer-chat-options')") is True
    page.keyboard.press('Escape')
    expect(sheet).to_be_hidden()
