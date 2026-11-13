"""Deleting a work package updates the tile, the evidence line and the roadmap board together.

An architect creates a parent work package with two child parts (three packages total).
Deleting the parent removes all three. The Work Packages tile, the evidence line under
the tiles and /genome/roadmap/ all read the same count after the delete, without a reload.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_capability_roadmap_figures_journey import BASE, PATH, _board, _frame, _page


pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _plan_parent_with_parts(browser, live_server, email, suffix):
    """Create one parent work package with two child parts; return parent id."""
    context, page = _page(browser, 1440, "light")
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Create a plateau
        status, plateau = api(page, "POST", BASE + "/plateaus", {"name": "Plateau " + suffix, "sequence_order": 1})
        assert status == 201, plateau
        plateau_id = plateau["plateau"]["id"]
        # Create parent work package
        status, parent = api(page, "POST", BASE + "/work-packages", {
            "name": "Parent " + suffix, "plateau_id": plateau_id,
            "start_date": "2026-11-01", "target_date": "2027-01-31"})
        assert status == 201, parent
        parent_id = (parent.get("work_package") or parent)["id"]
        # Create two child parts
        for i in range(2):
            status, child = api(page, "POST", "%s/work-packages/%d/children" % (BASE, parent_id), {
                "name": "Part %d " % (i + 1) + suffix})
            assert status in (200, 201), child
        return parent_id
    finally:
        context.close()


def test_delete_parent_updates_tile_evidence_and_board(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # Create a plateau
            status, plateau = api(page, "POST", BASE + "/plateaus", {"name": "Plateau " + suffix, "sequence_order": 1})
            assert status == 201, plateau
            plateau_id = plateau["plateau"]["id"]
            # Create parent work package
            status, parent = api(page, "POST", BASE + "/work-packages", {
                "name": "Parent " + suffix, "plateau_id": plateau_id,
                "start_date": "2026-11-01", "target_date": "2027-01-31"})
            assert status == 201, parent
            parent_id = (parent.get("work_package") or parent)["id"]
            # Create two child parts
            for i in range(2):
                status, child = api(page, "POST", "%s/work-packages/%d/children" % (BASE, parent_id), {
                    "name": "Part %d " % (i + 1) + suffix})
                assert status in (200, 201), child

            # Reload to get server-computed counts
            page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("3", timeout=PAGE_TIMEOUT)
            line_before = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: 3" in line_before, line_before
            _frame(page, "delete-debug-after-load", width, theme)

            # Check initial state on genome roadmap board
            board_text_before, board_count_before = _board(page, live_server)
            assert board_count_before == 3, board_count_before
            # Return to capability roadmap
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_be_visible(timeout=PAGE_TIMEOUT)

            # Delete the parent work package via the UI (cascade default true removes parts)
            wp_row = page.locator("tr").filter(has_text="Parent " + suffix)
            wp_row.get_by_role("button", name="Delete").first.click()
            # Confirm the deletion dialog
            page.get_by_role("alertdialog").get_by_role("button", name="Delete", exact=True).click()
            # Wait for the tile to update
            _frame(page, "delete-debug-after-delete", width, theme)

            # Without reload, the tile, evidence line and board must agree on 0
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("0", timeout=PAGE_TIMEOUT)
            line_after = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: none recorded" in line_after, line_after

            board_text_after, board_count_after = _board(page, live_server)
            assert board_count_after == 0, board_count_after

            _frame(page, "delete-parent", width, theme)
        finally:
            context.close()