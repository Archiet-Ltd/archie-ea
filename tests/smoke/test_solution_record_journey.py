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
            db.session.commit()
            out = {"solution_id": solution.id, "reviewed_id": reviewed.id, "org_id": org_id,
                   "score": compute(solution).label}
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
        shown = SCORE.search(page.get_by_test_id("record-score").inner_text())
        assert shown and shown.group(0) == demo["score"], (shown, demo["score"])
        next_step = page.get_by_test_id("record-next-step")
        assert next_step.is_visible() and "Next step" in next_step.inner_text()
        assert page.get_by_test_id("record-problem").inner_text().strip()
        changes = page.get_by_test_id("record-changes")
        assert changes.is_visible()
        added = int(page.get_by_test_id("changes-added").inner_text())
        changed = int(page.get_by_test_id("changes-changed").inner_text())
        assert added + changed >= 1, "the demo solution changes at least one application"
        # The actions an architect who did not author it may take.
        for testid in ("record-compare", "record-download", "record-ask"):
            assert page.get_by_test_id(testid).is_visible(), testid
        assert page.get_by_test_id("record-decide").count() == 0
        assert "/ai-chat?q=" in page.get_by_test_id("record-ask").get_attribute("href")
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "overview-1440.png"), full_page=True)

        # Every element on the Design tab opens its own record, which has content.
        page.get_by_role("link", name="Design", exact=True).click()
        page.wait_for_url(re.compile(r"tab=design"), timeout=PAGE_TIMEOUT)
        rows = page.locator("[data-testid^=design-table-] tbody tr")
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

        # Every other tab says what it will hold and offers the page that holds it today.
        for tab in ("requirements", "options", "risks", "delivery"):
            page.goto("%s/solutions/%d?tab=%s" % (live_server, sid, tab), wait_until="domcontentloaded")
            box = page.get_by_test_id("%s-next" % tab)
            assert box.is_visible(), tab
            assert box.get_by_role("link").first.get_attribute("href").startswith("/solutions/%d/" % sid)
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
        for tab in ("overview", "design", "review"):
            page.goto("%s/solutions/%d?tab=%s" % (live_server, sid, tab), wait_until="networkidle", timeout=PAGE_TIMEOUT)
            if theme == "dark":
                page.evaluate("document.documentElement.classList.add('dark')")
            assert page.get_by_test_id("tab-%s" % tab).is_visible()
            assert _no_overflow(page), "%s overflows at 390 wide" % tab
            page.screenshot(path=str(SHOTS / ("%s-%s-390.png" % (tab, theme))), full_page=True)
    finally:
        context.close()
