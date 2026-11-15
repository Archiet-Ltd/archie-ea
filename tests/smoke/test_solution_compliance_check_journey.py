"""Journey, in a browser: an architect opens a solution, goes to its Review tab and runs the compliance
analysis, and reads which controls the solution meets and which it misses, each with the framework it
comes from. A solution with no controls mapped says so and says what to do next.

Two organisations: the second holds a solution with controls of its own, and none of them reach the first
architect's screen.
"""
import os
import pathlib
import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("COMPLIANCE_SHOTS", "/tmp/compliance-check-shots"))

MAPPINGS_A = (
    ("ISO 27001", "A.9.1", "Access control policy is in place", "verified"),
    ("ISO 27001", "A.12.4", "Operations are logged and monitored", "verified"),
    ("GDPR", "Art. 32", "Personal data is encrypted at rest", "non_compliant"),
    ("GDPR", "Art. 30", "Records of processing are kept", "partial"),
    ("SOC 2", "CC6.1", "Logical access is reviewed each quarter", "not_assessed"),
)
MAPPINGS_B = (("HIPAA", "164.312", "Audit controls on patient records", "non_compliant"),)


def _solution(org_id, user_id, name, mappings):
    from app import create_app, db
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionComplianceMapping

    app = create_app("testing")
    with app.app_context():
        solution = Solution(name=name, organization_id=org_id, created_by_id=user_id)
        db.session.add(solution)
        db.session.flush()
        for framework, control, description, status in mappings:
            db.session.add(SolutionComplianceMapping(
                solution_id=solution.id, framework=framework, control_id=control,
                control_description=description, verification_status=status))
        db.session.commit()
        sid = solution.id
        db.session.remove()
    return sid


@pytest.fixture(scope="module")
def estate(live_server, seeded):
    a = create_fresh_org("solution_architect")
    b = create_fresh_org("solution_architect")
    uid_a = a["user_ids"]["solution_architect"]
    uid_b = b["user_ids"]["solution_architect"]
    return {
        "email": a["emails"]["solution_architect"],
        "other_email": b["emails"]["solution_architect"],
        "checked": _solution(a["org_id"], uid_a, "Plant Safety Platform", MAPPINGS_A),
        "empty": _solution(a["org_id"], uid_a, "Unmapped Pilot", ()),
        "other": _solution(b["org_id"], uid_b, "Clinic Records Platform", MAPPINGS_B),
    }


@pytest.fixture(scope="module")
def estate_two(live_server, seeded):
    """One person who belongs to two organisations and made a solution in the second, and a person of the
    first organisation only."""
    a = create_fresh_org("solution_architect")
    b = create_fresh_org("solution_architect")
    uid_a = a["user_ids"]["solution_architect"]
    from app import create_app, db
    from app.models.org_role import OrgRole

    app = create_app("testing")
    with app.app_context():
        OrgRole.set_role(b["org_id"], uid_a, "org_admin", granted_by_id=uid_a)
        db.session.commit()
        db.session.remove()
    return {
        "member_email": a["emails"]["solution_architect"],
        "outsider_email": create_fresh_org("solution_architect")["emails"]["solution_architect"],
        "second_org": b["org_id"],
        "second_solution": _solution(b["org_id"], uid_a, "Clinic Records Platform", MAPPINGS_B),
    }


def _context(browser, width, theme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


@pytest.mark.parametrize("width,theme", [(1440, "light"), (390, "dark")])
def test_the_architect_runs_the_compliance_check_and_reads_what_is_met_and_missed(
        browser, live_server, estate, width, theme):
    context = _context(browser, width, theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        sign_in(page, live_server, estate["email"])
        page.goto("%s/solutions/%d?tab=review" % (live_server, estate["checked"]), wait_until="networkidle",
                  timeout=PAGE_TIMEOUT)
        if theme == "dark":
            page.evaluate("document.documentElement.classList.add('dark')")
        panel = page.get_by_test_id("compliance-check")
        panel.scroll_into_view_if_needed()
        assert panel.is_visible()
        assert page.get_by_test_id("compliance-result").is_hidden()

        page.get_by_test_id("compliance-run").click()
        result = page.get_by_test_id("compliance-result")
        result.wait_for(state="visible")

        assert page.get_by_test_id("compliance-status").inner_text().strip() == "partial"
        assert page.get_by_test_id("compliance-summary").inner_text().strip() == \
            "2 met, 2 missed, 1 not assessed yet"
        met = page.get_by_test_id("compliance-met-item")
        assert met.count() == 2
        met_text = " ".join(met.all_inner_texts())
        assert "ISO 27001 A.9.1" in met_text and "Access control policy is in place" in met_text
        missed = page.get_by_test_id("compliance-missed-item")
        assert missed.count() == 2
        missed_text = " ".join(missed.all_inner_texts())
        assert "GDPR Art. 32" in missed_text and "Personal data is encrypted at rest" in missed_text
        assert "non compliant" in missed_text and "partial" in missed_text
        assert "SOC 2 CC6.1" in page.get_by_test_id("compliance-not-assessed").inner_text()
        text = result.inner_text()
        assert "HIPAA" not in text and "undefined" not in text and "null" not in text

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("compliance-%d-%s.png" % (width, theme))), full_page=True)
        assert page.evaluate("document.body.scrollWidth <= window.innerWidth + 1")
        assert not errors, errors
    finally:
        context.close()


def test_a_solution_with_no_controls_says_why_and_what_to_do(browser, live_server, estate):
    context = _context(browser, 1440, "light")
    page = context.new_page()
    try:
        sign_in(page, live_server, estate["email"])
        page.goto("%s/solutions/%d?tab=review" % (live_server, estate["empty"]), wait_until="networkidle",
                  timeout=PAGE_TIMEOUT)
        page.get_by_test_id("compliance-run").click()
        empty = page.get_by_test_id("compliance-empty")
        empty.wait_for(state="visible")
        assert "No controls are mapped to this solution yet" in empty.inner_text()
        assert page.get_by_test_id("compliance-summary").is_hidden()
    finally:
        context.close()


def _switch_organisation(page, live_server, org_id):
    """Switch the open organisation the way the account page does: a form post with the page's token."""
    status = page.evaluate(
        """async ([org]) => {
            const meta = document.querySelector('meta[name="csrf-token"]');
            const body = new URLSearchParams({organization_id: String(org)});
            if (meta) body.set('csrf_token', meta.content);
            const r = await fetch('/account/switch-organization', {method: 'POST', body, credentials: 'same-origin'});
            return r.status;
        }""", [org_id])
    assert status == 200, status


def test_a_member_of_two_organisations_runs_the_analysis_in_the_one_they_switched_to(
        browser, live_server, estate_two):
    context = _context(browser, 1440, "light")
    page = context.new_page()
    try:
        sign_in(page, live_server, estate_two["member_email"])
        page.goto("%s/account/manage" % live_server, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _switch_organisation(page, live_server, estate_two["second_org"])
        page.goto("%s/solutions/%d?tab=review" % (live_server, estate_two["second_solution"]),
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("compliance-run").click()
        page.get_by_test_id("compliance-result").wait_for(state="visible")
        assert page.get_by_test_id("compliance-status").inner_text().strip() == "non compliant"
        assert "HIPAA 164.312" in page.get_by_test_id("compliance-missed").inner_text()
    finally:
        context.close()


def test_a_member_of_one_organisation_gets_no_answer_for_another_organisations_solution(
        browser, live_server, estate_two):
    context = _context(browser, 1440, "light")
    page = context.new_page()
    try:
        sign_in(page, live_server, estate_two["outsider_email"])
        status, _ = api(page, "GET", "/solutions/%d/api/compliance-gap" % estate_two["second_solution"])
        assert status == 404
    finally:
        context.close()


def test_a_missed_control_with_a_gap_record_links_to_that_record(browser, live_server, estate):
    context = _context(browser, 1440, "light")
    page = context.new_page()
    try:
        sign_in(page, live_server, estate["email"])
        page.goto("%s/solutions/%d?tab=review" % (live_server, estate["checked"]), wait_until="networkidle",
                  timeout=PAGE_TIMEOUT)
        status, data = api(page, "POST", "/solutions/%d/api/compliance-gap" % estate["checked"], {})
        assert status == 200, data
        page.get_by_test_id("compliance-run").click()
        page.get_by_test_id("compliance-result").wait_for(state="visible")
        links = page.get_by_test_id("compliance-gap-link")
        assert links.count() == 2
        for icon in ("circle-check", "triangle-alert", "circle-help"):
            assert page.locator("[data-testid='compliance-result'] svg.lucide-%s" % icon).count() >= 1, icon
        href = links.first.get_attribute("href")
        page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        body = page.locator("main").inner_text()
        assert "GDPR" in body or "Art. 3" in body, body[:300]
    finally:
        context.close()
