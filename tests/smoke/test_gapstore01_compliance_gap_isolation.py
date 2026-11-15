"""Browser journey: an architect runs the compliance analysis on a solution
that has one control not met, gets that one gap back with its source, and
finds the same gap on the gap register. Another organisation, signed in, does
not see it on the register. A solution with no mapped controls records nothing
and says why.

The analysis panel's Run Analysis button lives in solutions/blueprint.html,
which no route renders, so the journey calls the endpoint the button calls,
as the signed-in person's own session, with the panel's reply shape.
"""
import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in, api

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REGISTER_PATH = "/enterprise/implementation/gap-analysis"


def _org_with_solution(role="enterprise_architect"):
    """An organisation with a solution that has one verified control mapping
    and one control that is not met; returns org info plus solution id."""
    from app import create_app, db
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionComplianceMapping

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
        db.session.flush()
        db.session.add(SolutionComplianceMapping(
            solution_id=solution.id, framework="GDPR", control_id="Art32-%s" % org["suffix"],
            element_name="Customer store", verification_status="non_compliant"))
        db.session.add(SolutionComplianceMapping(
            solution_id=solution.id, framework="GDPR", control_id="Art30-%s" % org["suffix"],
            element_name="Customer store", verification_status="verified"))
        db.session.commit()
        org["solution_id"] = solution.id
        empty = Solution(
            name="Empty solution %s" % org["suffix"], organization_id=org["org_id"],
            created_by_id=org["user_ids"][role], governance_status="draft", adm_phase="C")
        db.session.add(empty)
        db.session.commit()
        org["empty_solution_id"] = empty.id
    return org


def _open(page, base, path, ready):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)


def test_compliance_gap_org_isolation(browser, live_server):
    org_a = _org_with_solution()
    org_b = _org_with_solution()

    control = "Art32-%s" % org_a["suffix"]
    gap_name = "Compliance gap: GDPR %s (Journey solution %s, solution %d)" % (
        control, org_a["suffix"], org_a["solution_id"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        # Organisation A runs the analysis
        sign_in(page, live_server, org_a["emails"]["enterprise_architect"])
        url = "%s/solutions/%d/api/compliance-gap" % (live_server, org_a["solution_id"])
        status, data = api(page, "POST", url)
        assert status == 200 and data.get("success") is True, data
        assert data["compliance_status"] == "non_compliant", data
        assert [g["control_id"] for g in data["gaps"]] == [control], data
        assert data["gaps"][0]["source"] == "GDPR control %s, status non compliant" % control
        assert data["gaps"][0]["description"] == "GDPR control %s on Customer store is non compliant." % control

        # Organisation B opens the gap register and does not see A's gap
        context_b = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        page_b = context_b.new_page()
        try:
            sign_in(page_b, live_server, org_b["emails"]["enterprise_architect"])
            page_b.goto(live_server + REGISTER_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page_b.wait_for_selector("[data-testid='gaps-empty']", timeout=PAGE_TIMEOUT)
            assert control not in page_b.content(), \
                "organisation B sees organisation A's compliance gap"
        finally:
            context_b.close()

        # Organisation A opens the gap register and sees the same gap
        _open(page, live_server, REGISTER_PATH, "[data-testid='gap-row']")
        assert gap_name in page.content(), \
            "organisation A does not see their own compliance gap"
        assert "Art30-" not in page.content()

        # Running it again records nothing new
        status, data = api(page, "POST", url)
        assert status == 200 and [g["created"] for g in data["gaps"]] == [False], data
        _open(page, live_server, REGISTER_PATH, "[data-testid='gap-row']")
        assert page.locator("[data-testid='gap-row']").count() == 1

        # A solution with nothing mapped records nothing and says why
        status, data = api(page, "POST", "%s/solutions/%d/api/compliance-gap"
                           % (live_server, org_a["empty_solution_id"]))
        assert status == 200 and data["gaps"] == [], data
        assert "nothing to assess" in data["message"], data
        assert data["compliance_status"] == "not_assessed"
    finally:
        context.close()
