"""An architect works the Gaps page end to end.

In an organisation whose model holds a Gap element made outside the page, the architect opens the Gaps page and
finds it listed. They press "New gap", type a name, press Escape and are asked whether to discard what they typed;
Keep returns to the dialog with the text intact. They rate the gap High and create it: the page stays on the Gaps
page, the new gap is in the list, and the summary line speaks of the High rating. The simulations page then names
the gap in quotes, and its link lands on a page that shows the gap. A second organisation sees none of it.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/enterprise/implementation/gap-analysis"


def _seed_imported_gap(org_id, name):
    """A Gap element made outside the gap pages, the way an import or the model map makes one."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        db.session.add(ArchiMateElement(
            name=name, type="Gap", layer="Implementation", organization_id=org_id, source_kind="import",
            description="Came in with an import"))
        db.session.commit()
        db.session.remove()


def _frame(page, label, viewport, theme):
    out = os.environ.get("GAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/gaps-%s-%d-%s.png" % (out, label, viewport["width"], theme), full_page=True)


@pytest.mark.parametrize("viewport,theme", [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")])
def test_architect_sees_every_gap_and_makes_one_in_place(browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    imported = "Billing not live %s" % tag
    made = "Stacks gap %s" % tag
    _seed_imported_gap(org["org_id"], imported)
    _seed_imported_gap(other["org_id"], "Other organisation gap %s" % tag)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)

        # A slow network: the form's field settings arrive after the person has pressed New gap.
        def _slow(route):
            page.wait_for_timeout(2000)
            route.continue_()

        page.route("**/architecture/api/field-configs", _slow)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The gap made outside the page is listed, and opens its own page.
        expect(page.get_by_test_id("gaps-table")).to_be_visible(timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="gap-row"]', has_text=imported)
        expect(row).to_have_count(1)
        expect(page.get_by_text("No gaps recorded yet")).to_have_count(0)
        _frame(page, "imported", viewport, theme)

        # New gap opens the dialog here; Escape with typed text asks first.
        page.get_by_test_id("btn-new-gap").click()
        dialog = page.get_by_role("dialog", name=re.compile("Create gap"))
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.url.split("?")[0].endswith(PATH)
        # The rating opens on its default, never on the first option of the list.
        expect(dialog.get_by_label(re.compile("^Rating"))).to_have_value("medium")
        dialog.get_by_label("Element name").fill("")
        dialog.get_by_label("Element name").press_sequentially(made)
        dialog.get_by_label("Description").press_sequentially("Four of nine stacks")
        page.keyboard.press("Escape")
        ask = page.get_by_role("dialog", name="Discard what you typed?")
        expect(ask).to_be_visible(timeout=PAGE_TIMEOUT)
        _frame(page, "discard", viewport, theme)
        ask.get_by_role("button", name="Keep").click()
        expect(ask).to_have_count(0, timeout=PAGE_TIMEOUT)
        expect(dialog).to_be_visible()
        expect(dialog.get_by_label("Element name")).to_have_value(made)
        expect(dialog.get_by_label("Description")).to_have_value("Four of nine stacks")

        # Rate it High and create it: the page stays, the gap is in the list.
        dialog.get_by_label(re.compile("^Rating")).select_option("high")
        dialog.get_by_role("button", name="Create", exact=True).click()
        new_row = page.locator('[data-testid="gap-row"]', has_text=made)
        expect(new_row).to_have_count(1, timeout=PAGE_TIMEOUT)
        assert page.url.split("?")[0].endswith(PATH)
        expect(new_row).to_contain_text("High")
        expect(page.locator('[data-testid="gap-row"]', has_text=imported)).to_have_count(1)

        # The summary speaks of the ratings the list shows.
        notice = page.locator('[data-slot="data-table-notice"]')
        expect(notice).to_be_visible()
        summary = notice.inner_text().lower()
        assert "1 rated high" in summary and "1 not rated yet" in summary, summary
        assert "every gap has medium impact" not in page.locator("body").inner_text().lower()
        _frame(page, "created", viewport, theme)

        # Escape on a dialog with nothing typed closes at once.
        page.get_by_test_id("btn-new-gap").click()
        empty = page.get_by_role("dialog", name=re.compile("Create gap"))
        expect(empty).to_be_visible(timeout=PAGE_TIMEOUT)
        page.keyboard.press("Escape")
        expect(empty).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("dialog", name="Discard what you typed?")).to_have_count(0)

        # The simulations page names the gap in quotes, and its link lands on the gap.
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        action = page.locator('[data-testid="priority-action-link"]', has_text=made)
        expect(action).to_have_count(1, timeout=PAGE_TIMEOUT)
        assert action.inner_text().strip() == "Convert the gap \u201c%s\u201d to a work package" % made
        _frame(page, "simulations", viewport, theme)
        action.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).to_contain_text(made, timeout=PAGE_TIMEOUT)

        # A second organisation sees only its own gap.
        ctx2 = browser.new_context(ignore_https_errors=True, viewport=viewport)
        p2 = ctx2.new_page()
        try:
            sign_in(p2, live_server, other["emails"]["enterprise_architect"])
            p2.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            p2.wait_for_selector('[data-testid="gaps-table"]', timeout=PAGE_TIMEOUT)
            text = p2.content()
            assert "Other organisation gap %s" % tag in text
            assert imported not in text and made not in text
        finally:
            ctx2.close()
    finally:
        context.close()
