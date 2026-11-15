"""Browser journey: a compliance gap created in one organisation is not
visible to another organisation on the gap register.

Organisation A runs the compliance-gap analysis path
(/solutions/<id>/api/compliance-gap), which writes a gap into the one gap
register with A's organisation_id. Organisation B, signed in, opens the gap
register and does not see it. Organisation A does.

Failing at the line tip (the endpoint does not exist, and even if it did the
legacy writer lands in compliance_gaps, not gaps), green at head.
"""
import os

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in, api

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REGISTER_PATH = "/enterprise/implementation/gap-analysis"


def _org_with_solution(role="enterprise_architect"):
    """An organisation with a solution; returns org info plus solution id."""
    from app import create_app, db
    from app.models.solution_models import Solution

    org = create_fresh_org(role)
    with create_app("testing").app_context():
        solution = Solution(
            name="Journey solution %s" % org["suffix"],
            organization_id=org["org_id"],
            created_by_id=org["user_ids"][role],
            governance_status="draft",
            adm_phase="C",
        )
        db.session.add(solution)
        db.session.commit()
        org["solution_id"] = solution.id
    return org


def _open(page, base, path, ready):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)


def test_compliance_gap_org_isolation(browser, live_server):
    org_a = _org_with_solution()
    org_b = _org_with_solution()

    gap_name = "Compliance gap: Journey solution %s" % org_a["suffix"]

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        # Organisation A runs the compliance-gap path
        sign_in(page, live_server, org_a["emails"]["enterprise_architect"])
        status, data = api(page, "POST",
                           "%s/solutions/%d/api/compliance-gap" % (live_server, org_a["solution_id"]))
        assert status == 200, "compliance-gap API returned %s: %s" % (status, data)
        assert data.get("success") is True, "compliance-gap API did not succeed: %s" % data

        # Organisation B opens the gap register and does not see A's gap
        context_b = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        page_b = context_b.new_page()
        try:
            sign_in(page_b, live_server, org_b["emails"]["enterprise_architect"])
            page_b.goto(live_server + REGISTER_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page_b.wait_for_selector("[data-testid='gaps-empty']", timeout=PAGE_TIMEOUT)
            assert gap_name not in page_b.content(), \
                "organisation B sees organisation A's compliance gap"
        finally:
            context_b.close()

        # Organisation A opens the gap register and sees their gap
        _open(page, live_server, REGISTER_PATH, "[data-testid='gap-row']")
        assert gap_name in page.content(), \
            "organisation A does not see their own compliance gap"
    finally:
        context.close()
