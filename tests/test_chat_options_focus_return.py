"""A person who closes the Chat options sheet and starts typing in the composer loses no characters.

The sheet hands focus back to its opener in the same tick it closes, before the person can reach the
composer, so typing straight away is never interrupted. This keeps it that way."""
import pytest
from playwright.sync_api import expect

from tests.chat_opening_fixture import open_chat_options
from tests.test_ai_model_selector_display import browser, launch  # noqa: F401

pytestmark = pytest.mark.parametrize('browser', ['chromium'], indirect=True)
MODEL = {'model': 'fixture-0', 'display_name': 'Fixture 0', 'provider': 'openai',
         'recommended_for': [], 'is_fallback': False, 'fallback_order': 0, 'test_status': None}
TEXT = 'what breaks if payments fails'


def _close_with_escape(page):
    page.keyboard.press('Escape')


def _close_with_button(page):
    page.get_by_role('button', name='Close drawer').click()


def _close_with_backdrop(page):
    page.mouse.click(10, 500)


@pytest.mark.parametrize('close', [_close_with_escape, _close_with_button, _close_with_backdrop],
                         ids=['escape', 'close-button', 'backdrop'])
def test_typing_after_closing_the_sheet_keeps_every_character(launch, close):
    page, _ = launch([MODEL])
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog', name='Chat options')).to_be_hidden()
    open_chat_options(page)
    close(page)
    expect(page.get_by_role('dialog', name='Chat options')).to_be_hidden()
    # Nothing of the sheet is left behind in the modal manager.
    assert page.evaluate("Platform.modal.isOpen('drawer-chat-options')") is False
    assert page.evaluate("document.body.classList.contains('overflow-hidden')") is False
    # No layer root is left inert, and focus is back on the button that opened the sheet.
    assert page.evaluate("document.querySelectorAll('[data-modal-inert]').length") == 0
    expect(page.get_by_role('button', name='Chat options', exact=True)).to_be_focused()
    composer = page.locator('#user-input')
    composer.click()
    composer.press_sequentially('draft', delay=30)
    # Escape in the composer clears the draft by design; it must not also move focus away.
    page.keyboard.press('Escape')
    expect(composer).to_have_value('')
    composer.press_sequentially(TEXT, delay=30)
    expect(composer).to_have_value(TEXT)
    expect(composer).to_be_focused()
    expect(page.get_by_role('dialog', name='Chat options')).to_be_hidden()
