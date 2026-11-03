"""A requirement deleted from the backlog is gone from prioritisation.

An enterprise architect writes two requirements, sees both ranked on the
prioritisation page, deletes one on the backlog page through its own Delete
control and confirmation, and opens prioritisation again: only the kept one is
listed, on the board and in the tables, and still so after a reload. Another
organisation's requirement of the same name is untouched.
"""
import os
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BACKLOG = "/enterprise/requirements-backlog"
RANKED = "/backlog/prioritisation"


def _write_requirements(org, *names):
    from app import create_app, db
    from app.models.solution_architect_models import RequirementType, SolutionRequirement

    with create_app("testing").app_context():
        for name in names:
            db.session.add(SolutionRequirement(
                name=name, description="Written for the deletion journey.",
                requirement_type=RequirementType.FUNCTIONAL, organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()


def _frame(page, label, viewport, theme):
    out = os.environ.get("BACKLOG_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/prioritisation-%s-%d-%s.png" % (out, label, viewport["width"], theme), full_page=True)


@pytest.mark.parametrize("viewport,theme", [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")])
def test_a_deleted_requirement_leaves_prioritisation(browser, live_server, viewport, theme):
    tag = uuid.uuid4().hex[:6]
    kept, dropped = "Keep the ledger export %s" % tag, "Retire the fax gateway %s" % tag
    mine, theirs = create_fresh_org("enterprise_architect"), create_fresh_org("enterprise_architect")
    _write_requirements(mine, kept, dropped)
    _write_requirements(theirs, dropped)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
        page.goto(live_server + RANKED, wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='moscow-help']")
        page.wait_for_load_state("networkidle")
        body = page.locator("main").inner_text()
        assert kept in body and dropped in body

        page.goto(live_server + BACKLOG, wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid=requirements-table]")
        page.wait_for_function("() => !document.body.innerText.includes('Loading requirements')")
        page.locator("input[aria-label='Search requirements']").fill(tag)
        page.get_by_role("button", name="Edit requirement " + dropped).click()
        detail = page.locator("#req-detail-modal")
        detail.wait_for(state="visible")
        detail.get_by_role("button", name="Delete requirement").click()
        confirm = page.locator("#confirm-dialog-delete-requirement [role=alertdialog]")
        confirm.wait_for(state="visible")
        confirm.get_by_role("button", name="Delete requirement").click()
        detail.wait_for(state="hidden")
        page.get_by_text('Deleted "%s".' % dropped).wait_for(state="visible")

        for _ in range(2):  # and again after a reload
            page.goto(live_server + RANKED, wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid='moscow-help']")
            page.wait_for_load_state("networkidle")
            body = page.locator("main").inner_text()
            assert kept in body
            assert dropped not in body
        _frame(page, "after-deletion", viewport, theme)
    finally:
        context.close()

    other = browser.new_context(ignore_https_errors=True, viewport=viewport)
    p2 = other.new_page()
    try:
        sign_in(p2, live_server, theirs["emails"]["enterprise_architect"])
        p2.goto(live_server + RANKED, wait_until="domcontentloaded")
        p2.wait_for_selector("[data-testid='moscow-help']")
        p2.wait_for_load_state("networkidle")
        assert dropped in p2.locator("main").inner_text()
    finally:
        other.close()
