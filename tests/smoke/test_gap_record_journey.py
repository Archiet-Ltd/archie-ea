"""Gaps page: a new account adds a gap record from the empty state.

A person opens Changes > Gaps in an organisation with computed portfolio gaps and
no gap record, clicks "Add a gap record", fills in a name in the form that opens,
saves, lands back on the gaps page and finds the new gap as a row, with the three
figures agreeing and the unclosed figure not shown in a colour that reads as good.
A second organisation never sees the gap.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/adm-kanban/gap-analysis"


def _seed_phase():
    from app import create_app
    from app.models.adm_kanban import create_adm_phases

    with create_app("testing").app_context():
        create_adm_phases()


def _fresh_org_with_computed_gap():
    from .fresh_org import create_fresh_org

    org = create_fresh_org("enterprise_architect")
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        db.session.add(ApplicationComponent(
            name="Unmapped App %s" % org["suffix"], organization_id=org["org_id"],
            lifecycle_status="operational", description="Mapped to no capability."))
        db.session.commit()
        db.session.remove()
    return org


def _frame(page, label, viewport, theme):
    import os

    out = os.environ.get("GAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/gaps-%s-%d-%s.png" % (out, label, viewport["width"], theme), full_page=True)


def _figure(page, testid):
    text = page.locator('[data-testid="%s"]' % testid).inner_text().strip()
    return int(text)


@pytest.mark.parametrize("viewport,theme", [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")])
def test_add_a_gap_record_from_the_empty_state(browser, live_server, viewport, theme):
    from .fresh_org import sign_in

    _seed_phase()
    org = _fresh_org_with_computed_gap()
    email = org["emails"]["enterprise_architect"]
    name = "Gap record %s" % uuid.uuid4().hex[:6]

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="gaps-table"]')
        expect(table).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _figure(page, "gap-total") >= 1
        before_total = _figure(page, "gap-total")

        _frame(page, "before", viewport, theme)
        table.get_by_role("link", name=re.compile("Add a gap record", re.I)).click()

        # A form opens with the gap type chosen; fill the name and save.
        dialog = page.get_by_role("dialog", name="New Architecture Task")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog.locator("#ct-arch-element-type")).to_have_value("Gap")
        dialog.get_by_label("Title").fill(name)
        dialog.get_by_role("button", name=re.compile(r"^(Create|Save|Add)", re.I)).last.click()

        # Back on the gaps page, the new gap is a row.
        page.wait_for_url(lambda u: u.rstrip("/").endswith(PATH), timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="gap-row"]', has_text=name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text("No work package yet")

        # The three figures agree, and an unclosed gap is not shown as good.
        total, unclosed, covered = (_figure(page, t) for t in ("gap-total", "gap-unclosed", "gap-covered"))
        assert total == before_total + 1
        assert total == unclosed + covered
        assert unclosed >= 1
        assert "text-success" not in page.locator('[data-testid="gap-unclosed"]').get_attribute("class")
        assert "text-destructive" in page.locator('[data-testid="gap-unclosed"]').get_attribute("class")

        _frame(page, "after", viewport, theme)

        # Plain words.
        body = page.locator("main").inner_text() if page.locator("main").count() else page.content()
        assert "1 gaps" not in body
        assert "WorkPackage" not in body

        # Isolation: a second organisation does not see it.
        other = _fresh_org_with_computed_gap()
        ctx2 = browser.new_context(ignore_https_errors=True, viewport=viewport)
        p2 = ctx2.new_page()
        try:
            sign_in(p2, live_server, other["emails"]["enterprise_architect"])
            p2.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            p2.wait_for_selector('[data-testid="gaps-table"]', timeout=PAGE_TIMEOUT)
            assert name not in p2.content()
        finally:
            ctx2.close()
    finally:
        context.close()
