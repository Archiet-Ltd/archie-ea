"""An organisation administrator finds out why an AI tool call was refused.

From an empty organisation: an administrator models an element, and a person
with the read-only Viewer role asks for a governed fix to it from the Model
Health page, which queues the fix as an AI tool call awaiting approval. The
viewer then tries to run that tool call and is refused, because the Viewer
role does not include write access. The administrator opens the audit log,
filters to refused AI tool calls, and reads who was refused, which tool, and
the rule that refused it -- still there after a reload. Another
organisation's administrator sees none of it.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_launch_derived_relationships_journey import _create_element, _element_ids
from .test_launch_tech_radar_journey import _new_page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOOL = "apply_genome_patch"


def _set_role(user_id, role_name):
    """Give a person their platform role. People are created directly for a
    fresh organisation (see fresh_org); this sets the role they are created with."""
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        user = User.query.filter_by(id=user_id).one()
        user.role = Role.query.filter_by(name=role_name).one()
        db.session.commit()
        db.session.remove()


def _refused_rows(page):
    return page.locator("tbody tr", has_text="AI tool '%s' refused" % TOOL)


def _run_model_health_scan(org_id):
    """Run the drift detector and store the report for *org_id* so the
    model-health page renders findings without a synchronous scan."""
    from app import create_app, db
    from app.models.drift_report import DriftReport
    from app.modules.genome.services.drift_detector import detect_model_drift

    app = create_app("testing")
    with app.app_context():
        report = detect_model_drift(org_id)
        DriftReport.upsert(org_id, report)
        db.session.commit()
        db.session.remove()


def test_admin_sees_a_viewers_refused_tool_call_with_the_rule(browser, live_server):
    org = create_fresh_org("enterprise_architect", org_admin=True, extra_roles=("business_architect",))
    other = create_fresh_org("enterprise_architect", org_admin=True)
    admin_email = org["emails"]["enterprise_architect"]
    viewer_email = org["emails"]["business_architect"]
    _set_role(org["user_ids"]["enterprise_architect"], "Administrator")
    _set_role(org["user_ids"]["business_architect"], "Viewer")
    _set_role(other["user_ids"]["enterprise_architect"], "Administrator")
    name = "Claims Desk %s" % uuid.uuid4().hex[:6]

    # The administrator models an element; nothing is wired to it yet.
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, admin_email)
        _create_element(page, live_server, "business", "BusinessProcess", name)
        element_id = _element_ids(page, name)[name]
    finally:
        context.close()

    # Run the drift detector and store the report so the model-health page
    # shows the orphaned-element finding immediately (the detector is
    # scheduled, not run synchronously on page load).
    _run_model_health_scan(org["org_id"])

    # The viewer asks for the governed fix Model Health offers for it.
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, viewer_email)
        page.goto(live_server + "/genome/model-health", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        fix = page.locator("form.genome-drift-remediate").filter(
            has=page.locator("input[name=element_id][value='%d']" % element_id)
        ).filter(has=page.locator("input[name=finding_type][value='orphaned_element']"))
        expect(fix).to_have_count(1, timeout=PAGE_TIMEOUT)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            fix.get_by_role("button", name="Propose governed fix").click()

        status, body = api(page, "GET", "/ai-chat/approvals/pending")
        assert status == 200, body
        queued = [a for a in body["approvals"] if a.get("entity_type") == TOOL]
        assert len(queued) == 1, body
        approval_id = queued[0]["id"]

        # The viewer has no approve control on any screen (the approval panel
        # is offered only to people with write access), so running the queued
        # tool call goes through the approval interface that panel calls --
        # the way a stale tab or a script would reach it.
        status, body = api(page, "POST", "/ai-chat/approvals/%d/approve" % approval_id)
        assert status == 403, (status, body)
        assert "write" in body["error"]
    finally:
        context.close()

    # The administrator finds the refusal, with the rule, and it stays.
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, admin_email)
        assert page.goto(live_server + "/admin/audit-log", timeout=PAGE_TIMEOUT).status == 200
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("audit-refused-tool-calls").click()
        for attempt in range(2):
            row = _refused_rows(page)
            expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
            expect(row).to_contain_text(viewer_email)
            expect(row).to_contain_text("refused")
            row.click()
            detail = page.locator("[data-testid^='refused-tool-call-']")
            expect(detail).to_be_visible()
            expect(detail).to_contain_text(TOOL)
            expect(detail).to_contain_text("The Viewer role does not include write access")
            expect(detail).to_contain_text("Approving a queued tool call")
            expect(detail).to_contain_text(name)
            if attempt == 0:
                assert page.reload(timeout=PAGE_TIMEOUT).status == 200
    finally:
        context.close()

    # Another organisation's administrator sees none of it.
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        assert page.goto(live_server + "/admin/audit-log?action=tool_refused",
                         timeout=PAGE_TIMEOUT).status == 200
        expect(_refused_rows(page)).to_have_count(0)
        expect(page.get_by_text(viewer_email)).to_have_count(0)
        expect(page.get_by_text("Showing 0 of 0 entries")).to_be_visible()
    finally:
        context.close()
