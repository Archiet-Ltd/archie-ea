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


def test_typing_straight_after_closing_the_sheet_keeps_every_character(launch):
    page, _ = launch([MODEL])
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog', name='Chat options')).to_be_hidden()
    open_chat_options(page)
    page.keyboard.press('Escape')
    composer = page.locator('#user-input')
    composer.click()
    composer.press_sequentially(TEXT, delay=30)
    expect(composer).to_have_value(TEXT)
