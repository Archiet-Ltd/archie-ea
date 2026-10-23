"""An architect opens VRIO, McKinsey 7S and Porter's five forces, sees the
organisation's own elements placed in the right box, follows those entries to
a page with content, adds an element from the canvas and finds it in that box
after reload. A second organisation sees none of the first's elements.

The journey starts from the architect's goal — "read my model as each of the
three strategy frameworks, with my own resources and assessments already in
their boxes" — and asserts the outcome that goal needs: the right content in
the right box, every entry link landing on a page that shows the element, an
empty box explaining itself, and the figures agreeing. It is seeded with
linked data (elements carrying each box's profile) and run across two real
organisations for isolation.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_box_elements(org_id, placements):
    """Create native ArchiMate elements in *org_id* carrying each box's
    profile, the same shape add-from-canvas writes. ``placements`` is a list of
    ``(element_type, profile, name)``. Returns ``{name: element_id}``."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    ids = {}
    with app.app_context():
        for element_type, profile, name in placements:
            el = ArchiMateElement(
                organization_id=org_id,
                name=name,
                type=element_type,
                layer="Motivation",
                scope="enterprise",
                acm_properties={"profile": profile},
            )
            db.session.add(el)
            db.session.flush()
            ids[name] = el.id
        db.session.commit()
        db.session.remove()
    return ids


def _open(page, base, key):
    page.goto(base + "/strategy-canvas/%s" % key,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="strategy-canvas-%s"]' % key)).to_be_visible(
        timeout=PAGE_TIMEOUT
    )


def test_strategy_canvases_open_show_seeded_elements_and_add_lands(browser, live_server):
    org_a = create_fresh_org("enterprise_architect")
    org_b = create_fresh_org("enterprise_architect")
    suffix = org_a["suffix"]

    # Seed org A with a resource in VRIO's Valuable box, an element in 7S's
    # Shared values box, and a competitor in Porter's rivalry box.
    vrio_name = "Patented process %s" % suffix
    seven_s_name = "Customer-first culture %s" % suffix
    porter_name = "Two national rivals %s" % suffix
    ids = _seed_box_elements(org_a["org_id"], [
        ("Assessment", "vrio_valuable", vrio_name),
        ("Driver", "seven_s_shared_values", seven_s_name),
        ("Assessment", "porter_competitive_rivalry", porter_name),
    ])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org_a["emails"]["enterprise_architect"])

        # VRIO — the resource shows in the Valuable box; the other four boxes
        # explain their emptiness.
        _open(page, live_server, "vrio")
        valuable = page.get_by_test_id("canvas-entries-valuable")
        expect(valuable).to_contain_text(vrio_name, timeout=PAGE_TIMEOUT)
        for empty_box in ("rare", "inimitable", "organised", "competitive_implication"):
            expect(page.get_by_test_id("canvas-box-reason-%s" % empty_box)).to_contain_text(
                "Nothing here yet", timeout=PAGE_TIMEOUT
            )

        # The entry link lands on a page that shows the element by name — not a
        # 404, 403 or empty state.
        entry_link = page.get_by_test_id("canvas-entry-valuable-%d" % ids[vrio_name])
        entry_link.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(vrio_name, timeout=PAGE_TIMEOUT)

        # McKinsey 7S — the element shows in the central Shared values box.
        _open(page, live_server, "mckinsey_7s")
        expect(page.get_by_test_id("canvas-entries-shared_values")).to_contain_text(
            seven_s_name, timeout=PAGE_TIMEOUT
        )
        expect(page.get_by_test_id("seven-s-hard-group")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("seven-s-soft-group")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Porter's five forces — the competitor shows in the central rivalry box.
        _open(page, live_server, "porters_five_forces")
        expect(page.get_by_test_id("canvas-entries-competitive_rivalry")).to_contain_text(
            porter_name, timeout=PAGE_TIMEOUT
        )

        # Add an element from the canvas: it lands in that box after reload.
        added = "Low switching costs %s" % suffix
        box_input = page.get_by_test_id("canvas-add-input-buyer_power")
        box_input.fill(added)
        page.get_by_test_id("canvas-add-submit-buyer_power").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        _open(page, live_server, "porters_five_forces")
        expect(page.get_by_test_id("canvas-entries-buyer_power")).to_contain_text(
            added, timeout=PAGE_TIMEOUT
        )
    finally:
        context.close()

    # A second organisation sees none of the first's elements.
    context_b = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context_b.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page_b = context_b.new_page()
    try:
        sign_in(page_b, live_server, org_b["emails"]["enterprise_architect"])
        _open(page_b, live_server, "vrio")
        expect(page_b.locator("body")).not_to_contain_text(vrio_name, timeout=PAGE_TIMEOUT)
        # Its Valuable box is empty and says so.
        expect(page_b.get_by_test_id("canvas-box-reason-valuable")).to_contain_text(
            "Nothing here yet", timeout=PAGE_TIMEOUT
        )
        _open(page_b, live_server, "porters_five_forces")
        expect(page_b.locator("body")).not_to_contain_text(porter_name, timeout=PAGE_TIMEOUT)
    finally:
        context_b.close()
