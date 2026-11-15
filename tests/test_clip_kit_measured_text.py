"""The smallest text a standard image records is measured from the rendered page, not written in as a constant."""
from __future__ import annotations

import pytest

from scripts.capture import clip_kit

playwright_sync = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page():
    with playwright_sync.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


def test_the_smallest_text_is_what_the_page_renders(page):
    page.set_content('<div id="t"><p style="font-size:20px">Large</p><p style="font-size:13px">Small</p>'
                     '<p style="font-size:30px">Larger</p></div>')
    measured = page.locator("#t").evaluate(clip_kit._SMALL_TEXT_JS)
    assert measured["smallest"] == 13
    assert measured["small"] == []


def test_text_under_the_standard_is_listed_and_hidden_text_is_not(page):
    page.set_content('<div id="t"><p style="font-size:14px">Fine</p><p style="font-size:9px">Tiny</p>'
                     '<p style="font-size:7px;display:none">Hidden</p></div>')
    measured = page.locator("#t").evaluate(clip_kit._SMALL_TEXT_JS)
    assert measured["smallest"] == 9
    assert measured["small"] == ["9px: Tiny"]
