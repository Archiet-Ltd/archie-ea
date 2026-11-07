"""An architect opens the consolidation candidates left from before the overlap rule and reads only believable pairs.

In the seeded Lantern Quay Systems organisation, pairs of unrelated systems were proposed at 100% with no
reason. After the re-score runs (as it does on every deploy) the person opens the list, without running a new
comparison, and finds those pairs dismissed with the explanation, and every pair still waiting for a decision
stating what the two systems share.
"""
import re

import pytest

from .conftest import PAGE_TIMEOUT
from .test_acc7opp_journey import DEMO_EMAIL, VIEWPORTS, _shot, demo, stale_pairs  # noqa: F401  (fixtures)
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_old_pairs_are_rescored_before_anyone_runs_a_new_comparison(
    browser, live_server, app, demo, stale_pairs, viewport, scheme  # noqa: F811
):
    from app.commands.rescore_consolidation_candidates import rescore_consolidation_candidates_command

    result = app.test_cli_runner().invoke(rescore_consolidation_candidates_command)
    assert result.exit_code == 0, result.output
    assert "dismissed" in result.output, result.output

    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("candidate-row")
        assert rows.count() == stale_pairs
        for index in range(rows.count()):
            text = re.sub(r"\s+", " ", rows.nth(index).inner_text())
            assert "Not set" not in text, text
            assert "Event Relay" in text, text
            assert re.search(r"reject|dismiss", text, re.I), text
            assert "100%" not in text, text
        assert "nothing supports the pairing" in page.locator("body").inner_text()
        _shot(page, "candidates-rescored", viewport["width"])
        if viewport["width"] <= 390:
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        page.close()
