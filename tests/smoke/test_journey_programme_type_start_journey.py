"""R1-04 / SDD 13 R1 test 2: an enterprise architect starts a journey with a
programme type under the test override, sees counts before submit, and the
type survives to the workspace and a reload.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

from .test_archetype_journeys import _login, _visit, page  # noqa: F401


def test_enterprise_architect_starts_a_journey_with_a_programme_type(
    page, live_server, seeded
):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, "/architecture-journey/")

    select = page.locator("#journey-programme-type")
    assert select.count() == 1, "programme type selector must render under the test override"

    page.fill("#journey-title", "S/4HANA smoke journey")
    # The purpose radio and architecture-scope checkbox inputs are visually
    # hidden (sr-only) behind a styled <label>; force the underlying input
    # rather than the label, matching this suite's existing pattern for
    # Alpine-bound sr-only controls.
    page.locator('input[name="intent"]').first.check(force=True)
    page.locator('input[type="checkbox"][value="motivation"]').check(force=True)

    select.select_option("s4hana")
    counts = page.locator('[data-testid="programme-type-counts"]')
    counts.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    counts_text = counts.inner_text()
    assert "workstream" in counts_text
    assert "deliverable" in counts_text
    assert "gate" in counts_text

    page.locator('button[type="submit"]', has_text="Start architecture journey").click(
        force=True, no_wait_after=True
    )
    page.wait_for_url("**/architecture-journey/work/**", timeout=PAGE_TIMEOUT)

    badge = page.locator('[data-testid="journey-programme-type"]')
    assert badge.count() == 1
    assert "SAP S/4HANA transformation" in badge.inner_text()

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    badge = page.locator('[data-testid="journey-programme-type"]')
    assert badge.count() == 1
    assert "SAP S/4HANA transformation" in badge.inner_text()
