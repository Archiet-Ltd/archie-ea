"""Journey, in a browser: a solution opens on its record, and the record states the conclusion.

The demonstration organisation is seeded by the product's own seed. Three people use it:

* a solution architect of that organisation (persona solution_architect, platform role Architect, not an
  administrator) finds "Field Calibration Platform" from the Solutions list, reads what it changes, who owns
  it, its design score and its next step, opens the Design tab and clicks through to the Calibration Ledger's
  own record, which has content. The old addresses of the review packet, the conformance review and the
  completeness report land on the matching tab;
* a review board member sees Decide on a solution the board is reviewing;
* the author of that solution never sees Decide.

A fourth test proves another organisation never opens the record. The phone width is checked for overflow.

Slice 5b: the same architect reads the Requirements tab (each requirement beside the element that realises it,
the matrix one click away), the Options and decision tab (the leader, the comparison, the decision), the Risks
tab (the register's risks with owners, a grid that counts every one, the register one click away) and the
Delivery tab (the work packages, what each depends on, the roadmap); the old addresses of the heat map and the
communication log land on the record; and the author records a decision note in the side column.
"""
import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOLUTION = "Field Calibration Platform"
IN_REVIEW = "Event-Driven Operations Backbone"
LEDGER = "Calibration Ledger"
AUTHOR_EMAIL = "demo@lantern-quay.example.com"
ARCHITECT_EMAIL = "sol.architect@lantern-quay.example.com"
MEMBER_EMAIL = "board.member@lantern-quay.example.com"
SHOTS = pathlib.Path(os.environ.get("SOL5A_SHOTS", "/tmp/sol5a-shots"))
SCORE = re.compile(r"(\d+) of 10")
OPTIONS = [
    {"name": "Extend the Calibration Ledger", "approach": "extend",
     "summary": "Extend the ledger the field app already writes to, and add a signing service.",
     "pros": ["One store of readings", "No new vendor"], "cons": ["Two field app releases"],
     "cost_band": "low", "timeline_months": 4, "risk": "medium", "recommended": True},
    {"name": "Buy a calibration suite", "approach": "buy",
     "summary": "A packaged suite with its own data model and certificate store.",
     "pros": ["Certificates out of the box"], "cons": ["A second store of readings", "Nine months"],
     "cost_band": "high", "timeline_months": 9, "risk": "low", "recommended": False},
    {"name": "Keep the paper sheets and scan them", "approach": "reuse",
     "summary": "Scan the job sheets into the document store and key the readings later.",
     "pros": ["Nothing to build"], "cons": ["Readings still keyed twice", "No as-found history"],
     "cost_band": "low", "timeline_months": 1, "risk": "high", "recommended": False},
]


@pytest.fixture(scope="module")
def demo(live_server, seeded):
    """Seed the demonstration organisation, add an architect and a board member, and make sure the
    solution under review has an open review item the board can decide."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.architecture_review_board import ARBReviewItem
            from app.models.solution_models import Solution
            from app.models.user import Role, User
            from app.services.solution_design_score_service import compute

            author = User.query.filter_by(email=AUTHOR_EMAIL).one()
            author.password = PASSWORD  # the seed leaves an existing demo user's password alone
            author.confirmed = True
            org_id = author.organization_id
            role = Role.query.filter_by(name="Architect").one()
            for email, first, enterprise_role in ((ARCHITECT_EMAIL, "Sol", "solution_architect"),
                                                  (MEMBER_EMAIL, "Board", "arb_member")):
                user = User.query.filter_by(email=email).first()
                if user is None:
                    user = User(email=email, first_name=first, last_name="Member" if first == "Board" else "Architect",
                                organization_id=org_id, enterprise_role=enterprise_role, confirmed=True)
                    user.role = role
                    user.password = PASSWORD
                    db.session.add(user)
            solution = Solution.query.filter_by(name=SOLUTION, organization_id=org_id).one()
            if not (solution.solution_owner or "").strip():
                solution.solution_owner = "Ivo Reed"  # the seed leaves an existing solution's owner alone
            reviewed = Solution.query.filter_by(name=IN_REVIEW, organization_id=org_id).one()
            reviewed.governance_status = "arb_review"
            if reviewed.created_by_id != author.id:
                reviewed.created_by_id = author.id
            if ARBReviewItem.query.filter_by(solution_id=reviewed.id).count() == 0:
                db.session.add(ARBReviewItem(
                    review_number="LQ-ARB-J5A", title=reviewed.name, review_type="solution_design",
                    status="submitted", organization_id=org_id, submitter_id=author.id,
                    solution_id=reviewed.id,
                ))
            # Slice 5b: the four tabs and the notes have something true to show on the demo solution.
            import json as _json
            from datetime import date as _date, datetime as _datetime
            from app.models.archimate_core import ArchiMateElement
            from app.models.architecture_decision import ArchitectureDecision
            from app.models.unified_work_package import UnifiedWorkPackage
            from app.models.solution_architect_models import SolutionRequirement
            from app.models.stakeholder_communication import StakeholderCommunication
            from app.modules.solutions_strategic.v2.services import solution_record_service as record
            from app.services import communication_log_service

            ledger = ArchiMateElement.query.filter_by(organization_id=org_id, name=LEDGER).first()
            first_req = (SolutionRequirement.query.filter_by(solution_id=solution.id)
                         .order_by(SolutionRequirement.id).first())
            if ledger is not None and first_req is not None and not first_req.archimate_element_id:
                first_req.archimate_element_id = ledger.id
            if ArchitectureDecision.query.filter_by(solution_id=solution.id).count() == 0:
                db.session.add(ArchitectureDecision(
                    title="Extend the Calibration Ledger rather than buy a suite", organization_id=org_id,
                    solution_id=solution.id, enterprise_level=False, status="accepted",
                    context="Readings live in two systems and certificates are typed twice.",
                    decision="Extend the Calibration Ledger and retire the paper job sheets.",
                    rationale="It keeps one store of readings and reuses the field app engineers already carry.",
                    consequences="The field app changes in two releases; no new vendor; the ledger needs a signing service.",
                    alternatives=_json.dumps(OPTIONS), estimated_effort="Medium", business_value="High",
                    created_by_id=author.id, decided_by_id=author.id, decided_at=_datetime.utcnow(),
                ))
            if UnifiedWorkPackage.query.filter_by(context="solution", context_id=solution.id).count() == 0:
                migration = UnifiedWorkPackage(
                    name="Ledger migration", context="solution", context_id=solution.id, organization_id=org_id,
                    status="completed", progress_percentage=100, start_date=_date(2026, 11, 2),
                    end_date=_date(2026, 12, 4), owner_id=author.id)
                db.session.add(migration)
                db.session.flush()
                rollout = UnifiedWorkPackage(
                    name="Field app rollout", context="solution", context_id=solution.id, organization_id=org_id,
                    status="in_progress", progress_percentage=40, start_date=_date(2026, 12, 7),
                    end_date=_date(2027, 2, 26), work_dependencies=[migration.id], owner_id=author.id)
                db.session.add(rollout)
                db.session.flush()
                db.session.add(UnifiedWorkPackage(
                    name="Certificate signing service", context="solution", context_id=solution.id,
                    organization_id=org_id, status="planned", progress_percentage=0,
                    start_date=_date(2027, 3, 1), work_dependencies=[rollout.id]))
            db.session.commit()
            if StakeholderCommunication.query.filter_by(solution_id=solution.id).count() == 0:
                communication_log_service.log_communication(
                    solution_id=solution.id, stakeholder_id=None, comm_type="meeting",
                    subject="Kick-off with field service",
                    summary="Agreed the pilot region and the two-release rollout.",
                    outcome=None, action_items=[], logged_by=author.id)
            out = {"solution_id": solution.id, "reviewed_id": reviewed.id, "org_id": org_id,
                   "score": compute(solution).label,
                   "requirements": len(record.requirements_for(solution, record.design_groups(solution))["rows"]),
                   "risks": len(record.risks_for(solution, record.design_groups(solution))["rows"])}
            db.session.remove()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return out


def _context(browser, width=1440, theme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


def _no_overflow(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('body, main, [data-testid=solution-record]'))"
        ".every(el => el.scrollWidth <= window.innerWidth + 1)"
    )


def test_the_architect_reads_the_record_and_reaches_an_element(browser, live_server, demo):
    sid = demo["solution_id"]
    context = _context(browser)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        sign_in(page, live_server, ARCHITECT_EMAIL)

        # Find the solution from the list and open it.
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=SOLUTION, exact=True).first.click()
        page.wait_for_url(re.compile(r"/solutions/%d(\?|$|#)" % sid), timeout=PAGE_TIMEOUT)
        record = page.locator("[data-testid=solution-record]")
        record.wait_for(state="visible")

        # The header states the conclusion: owner, stage, score, and the next step.
        assert "Ivo Reed" in page.get_by_test_id("record-owner").inner_text()
        assert page.get_by_test_id("record-stage").inner_text().strip()
        shown = SCORE.search(" ".join(page.get_by_test_id("record-score").inner_text().split()))
        assert shown and shown.group(0) == demo["score"], (shown, demo["score"])
        next_step = page.get_by_test_id("record-next-step")
        assert next_step.is_visible() and "Next step" in next_step.inner_text()
        assert page.get_by_test_id("record-problem").inner_text().strip()
        changes = page.get_by_test_id("record-changes")
        assert changes.is_visible()
        added = int(page.get_by_test_id("changes-added").inner_text())
        changed = int(page.get_by_test_id("changes-changed").inner_text())
        assert added + changed >= 1, "the demo solution changes at least one application"
        # The facts about the record: the set properties show their values; the unset ones are named once, with a way to set them.
        facts = page.get_by_test_id("record-properties")
        unset = page.get_by_test_id("record-properties-unset")
        named = facts.locator("dt").count() + (len(unset.inner_text().split(":", 1)[1].split(",")) if unset.count() else 0)
        assert named >= 5
        if unset.count():
            assert unset.get_by_role("link", name="Edit").count() == 1
        assert "Chief Operations Officer" in page.get_by_test_id("property-sponsor").inner_text()
        for value in facts.locator("dd").all_inner_texts():
            assert value.strip(), "a property shows an empty value"
        # The actions an architect who did not author it may take, in the More menu.
        page.get_by_test_id("record-more").click()
        for testid in ("record-compare", "record-download", "record-ask"):
            assert page.get_by_test_id(testid).is_visible(), testid
        assert page.get_by_test_id("record-decide").count() == 0
        assert "/ai-chat?q=" in page.get_by_test_id("record-ask").get_attribute("href")
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "overview-1440.png"), full_page=True)

        # Every element on the Design tab opens its own record, which has content.
        page.get_by_role("link", name="Design", exact=True).click()
        page.wait_for_url(re.compile(r"tab=design"), timeout=PAGE_TIMEOUT)
        rows = page.locator("[data-testid^=design-table-] tbody tr[data-row]")
        summary = page.get_by_test_id("design-summary").inner_text()
        assert str(rows.count()) in summary, (rows.count(), summary)
        assert rows.count() >= 10
        page.screenshot(path=str(SHOTS / "design-1440.png"), full_page=True)
        page.get_by_role("link", name=LEDGER, exact=True).first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/solutions/" not in page.url
        heading = page.get_by_role("heading", name=re.compile(LEDGER)).first
        heading.wait_for(state="visible")
        assert len(page.locator("main").inner_text()) > 200, "the element's record is empty"

        # The old addresses land on the matching tab.
        for path, tab, marker in (("/review-packet", "review", "review-verdict"),
                                  ("/conformance", "review", "review-findings"),
                                  ("/completeness", "overview", "record-score")):
            page.goto("%s/solutions/%d%s" % (live_server, sid, path), wait_until="domcontentloaded")
            assert ("tab=%s" % tab in page.url) or (tab == "overview" and page.url.rstrip("/").endswith(str(sid))), (path, page.url)
            assert page.get_by_test_id(marker).first.is_visible(), (path, marker)
        # The Review tab shows the same score as the header.
        page.goto("%s/solutions/%d?tab=review" % (live_server, sid), wait_until="domcontentloaded")
        assert demo["score"] in page.get_by_test_id("review-verdict").locator("xpath=..").inner_text()
        assert page.get_by_test_id("review-history").is_visible()
        page.screenshot(path=str(SHOTS / "review-1440.png"), full_page=True)

        # Requirements sit beside the element that realises them; the matrix is one click away.
        page.goto("%s/solutions/%d?tab=requirements" % (live_server, sid), wait_until="domcontentloaded")
        rows = page.locator("[data-testid=requirement-row]")
        assert rows.count() == demo["requirements"], (rows.count(), demo["requirements"])
        assert rows.count() >= 5, "the demo solution holds requirements in the model and on the record"
        assert LEDGER in [t.strip() for t in page.locator("[data-testid=requirement-element-link]").all_inner_texts()]
        # a requirement from the model opens its own record
        model_link = page.get_by_test_id("requirement-link").first
        assert model_link.get_attribute("href") and "/solutions/" not in model_link.get_attribute("href")
        assert page.locator("[data-testid=motivation-row]").count() >= 1
        assert page.get_by_test_id("requirements-composer").is_visible()
        page.screenshot(path=str(SHOTS / "requirements-1440.png"), full_page=True)
        page.get_by_test_id("requirements-traceability").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/traceability" in page.url and len(page.locator("main").inner_text()) > 200

        # Options and decision: the leader, the comparison and the decision record.
        page.goto("%s/solutions/%d?tab=options" % (live_server, sid), wait_until="domcontentloaded")
        assert page.locator("[data-testid=option-row]").count() == len(OPTIONS)
        assert "Recommended" in page.get_by_test_id("options-recommendation").inner_text()
        assert "one store of readings" in page.get_by_test_id("options-decision").inner_text()
        page.screenshot(path=str(SHOTS / "options-1440.png"), full_page=True)

        # Risks: the register's risks with owners; the grid counts every one; the register opens.
        page.goto("%s/solutions/%d?tab=risks" % (live_server, sid), wait_until="domcontentloaded")
        risk_rows = page.locator("[data-testid=risk-row]")
        assert risk_rows.count() == demo["risks"], (risk_rows.count(), demo["risks"])
        counts = page.locator("[data-testid=risk-cell]").evaluate_all("els => els.map(e => e.dataset.count)")
        assert sum(int(c) for c in counts) == demo["risks"], counts
        assert all(o.strip() for o in page.locator("[data-testid=risk-owner]").all_inner_texts())
        first_risk = page.get_by_test_id("risk-link").first.inner_text().strip()
        page.screenshot(path=str(SHOTS / "risks-1440.png"), full_page=True)
        page.get_by_test_id("risks-register").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.startswith(live_server + "/risks/"), page.url
        assert first_risk in page.locator("main").inner_text()

        # Delivery: the work packages, what each depends on and what needs it, and the roadmap.
        page.goto("%s/solutions/%d?tab=delivery" % (live_server, sid), wait_until="domcontentloaded")
        assert page.locator("[data-testid=work-package-row]").count() == 3
        assert "Ledger migration" in page.locator("[data-testid=work-package-depends]").nth(1).inner_text()
        assert "Field app rollout" in page.locator("[data-testid=work-package-needed]").nth(0).inner_text()
        assert "1 done, 1 under way, 1 not started" in page.get_by_test_id("delivery-summary").inner_text()
        page.screenshot(path=str(SHOTS / "delivery-1440.png"), full_page=True)
        page.get_by_test_id("delivery-roadmap").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/migration-roadmap" in page.url and len(page.locator("main").inner_text()) > 100

        # The old addresses of the heat map and the communication log land on the record.
        page.goto("%s/solutions/%d/risks/heatmap" % (live_server, sid), wait_until="domcontentloaded")
        assert "tab=risks" in page.url and page.get_by_test_id("risks-grid").is_visible()
        page.goto("%s/solutions/%d/communications" % (live_server, sid), wait_until="domcontentloaded")
        assert page.url.endswith("/solutions/%d#record-notes" % sid), page.url
        assert page.get_by_test_id("note-form").is_visible()
        assert not errors, errors
    finally:
        context.close()


def test_a_board_member_sees_decide_and_the_author_does_not(browser, live_server, demo):
    rid = demo["reviewed_id"]
    url = "%s/solutions/%d?tab=review" % (live_server, rid)
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, MEMBER_EMAIL)
        page.goto(url, wait_until="domcontentloaded")
        decide = page.get_by_test_id("review-decide")
        assert decide.is_visible()
        assert page.get_by_test_id("record-decide").is_visible()
        assert re.search(r"/arb/reviews/\d+", decide.get_attribute("href"))
        assert page.get_by_test_id("record-submit").count() == 0
        assert "LQ-ARB" in page.get_by_test_id("review-history").inner_text()
    finally:
        context.close()
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, AUTHOR_EMAIL)
        page.goto(url, wait_until="domcontentloaded")
        assert page.get_by_test_id("review-verdict").is_visible()
        assert page.get_by_test_id("review-decide").count() == 0
        assert page.get_by_test_id("record-decide").count() == 0
        assert "Awaiting the board" in page.get_by_test_id("tab-review").inner_text()
    finally:
        context.close()


def test_the_author_records_a_decision_note(browser, live_server, demo):
    sid = demo["solution_id"]
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, AUTHOR_EMAIL)
        page.goto("%s/solutions/%d" % (live_server, sid), wait_until="domcontentloaded")
        notes = page.get_by_test_id("record-notes-list")
        assert "Kick-off with field service" in notes.inner_text()
        before = page.locator("[data-testid=note-item]").count()
        form = page.get_by_test_id("note-form")
        form.locator("[name=kind]").select_option("decision")
        form.locator("[name=subject]").fill("Pilot in the north region first")
        form.locator("[name=summary]").fill("Agreed with field service on 25 October.")
        form.get_by_test_id("note-add").click()
        page.wait_for_function("n => document.querySelectorAll('[data-testid=note-item]').length === n",
                               arg=before + 1, timeout=PAGE_TIMEOUT)
        first = page.locator("[data-testid=note-item]").first.inner_text()
        assert "Decision" in first and "Pilot in the north region first" in first, first
        page.screenshot(path=str(SHOTS / "notes-1440.png"), full_page=True)
    finally:
        context.close()


def test_another_organisation_never_opens_the_record(browser, live_server, demo):
    other = create_fresh_org("solution_architect")
    sid = demo["solution_id"]
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, other["emails"]["solution_architect"])
        for path in ("/solutions/%d" % sid, "/solutions/%d?tab=review" % sid,
                     "/solutions/%d/review-packet" % sid, "/solutions/%d/completeness" % sid):
            response = page.goto(live_server + path, wait_until="domcontentloaded")
            assert response.status in (403, 404), (path, response.status)
            assert SOLUTION not in page.content(), path
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded")
        assert SOLUTION not in page.content()
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_record_fits_a_phone(browser, live_server, demo, theme):
    sid = demo["solution_id"]
    context = _context(browser, width=390, theme=theme)
    page = context.new_page()
    try:
        sign_in(page, live_server, ARCHITECT_EMAIL)
        SHOTS.mkdir(parents=True, exist_ok=True)
        for tab in ("overview", "design", "requirements", "options", "risks", "review", "delivery"):
            page.goto("%s/solutions/%d?tab=%s" % (live_server, sid, tab), wait_until="networkidle", timeout=PAGE_TIMEOUT)
            if theme == "dark":
                page.evaluate("document.documentElement.classList.add('dark')")
            assert page.get_by_test_id("tab-%s" % tab).is_visible()
            assert _no_overflow(page), "%s overflows at 390 wide" % tab
            page.screenshot(path=str(SHOTS / ("%s-%s-390.png" % (tab, theme))), full_page=True)
    finally:
        context.close()
