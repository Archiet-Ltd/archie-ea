"""A signed-in architect reads one trail, with no label twice, on pages that used to repeat one.

The goal: know where the page sits. The Gaps page passed its own section crumb and the sidebar drew it
again, so the trail read Home > Changes > Changes > Gaps.
"""
import os

import pytest

from tests.smoke.test_archetype_journeys import _login

PAGE_TIMEOUT = 30000


def _trail(page):
    items = page.locator('nav[aria-label="Breadcrumb"] ol li:visible').all_inner_texts()
    return [" ".join(t.split()) for t in items if t.strip()]


@pytest.mark.smoke
@pytest.mark.parametrize("width", [1440, 390])
def test_gaps_page_trail_names_changes_once_and_ends_on_gaps(browser, live_server, seeded, width):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/adm-kanban/gap-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip(), "the Gaps page shows a heading"
        shots = os.environ.get("SMOKE_WALK_DIR")
        if shots:
            page.screenshot(path=os.path.join(shots, "gaps-%d.png" % width))
        if width >= 640:
            trail = _trail(page)
            assert trail[0] == "Home" and trail[1] == "Changes"
            assert trail[-1].lower().startswith("gap")
            assert [t.lower() for t in trail].count("changes") == 1
            assert len(trail) == len({t.lower() for t in trail})
        else:
            back = page.locator('nav[aria-label="Breadcrumb"] a:visible').first
            assert back.inner_text().strip() in ("Home", "Changes")
            back.click()
            page.wait_for_load_state("domcontentloaded")
            assert page.locator("h1, h2").first.inner_text().strip(), "the parent page has content"
    finally:
        context.close()
