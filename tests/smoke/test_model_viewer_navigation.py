"""Real authenticated empty-model Back navigation, with no HTTP doubles."""
import os
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize('width', [390, 1280, 1440])
def test_empty_model_viewer_back_returns_to_assistant(browser, live_server, seeded, width, tmp_path):
    # A fresh context has no assistant data or cached model; no storage override
    # is needed to reproduce the first-visit state.
    page = browser.new_page(viewport={'width': width, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        response = page.goto(live_server + '/architecture-assistant/model-viewer', timeout=PAGE_TIMEOUT)
        assert response.status == 200
        expect(page.get_by_text(
            'No architecture assistant data found. Please run the assistant first.', exact=True
        )).to_be_visible()
        shots = os.environ.get('SMOKE_WALK_DIR') or str(tmp_path)
        page.screenshot(path=os.path.join(shots, 'model-viewer-empty-%d.png' % width))
        with page.expect_navigation(wait_until='domcontentloaded', timeout=PAGE_TIMEOUT) as navigation:
            page.get_by_role('button', name='Back to Architecture Assistant', exact=True).click()
        assert navigation.value.status == 200
        expect(page).to_have_url(live_server + '/architecture-assistant/')
        expect(page.get_by_role('heading', name='Architecture Assistant', exact=True, level=1)).to_be_visible()
        assert errors == [], errors
    finally:
        page.close()
