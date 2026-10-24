"""Vendor options analysis: an architect reviews the comparison and records a requirement,
a second person records the decision, and both survive a reload.

The page is built from the shared component macros. This drives its real controls
(the list link, the wizard steps, the Requirements Matrix form, the decision form and
its confirmation dialog) and asserts the outcome each person needs: the vendors and
scores they were given are on screen and agree with the stored figures, a new
requirement is still there after a reload, and the recorded decision shows on the
analysis afterwards. A new analysis with nothing chosen explains why the later steps
are locked.
"""
import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_VENDORS = [("Northwind ERP", 82.5), ("Contoso Suite", 74.0), ("Fabrikam Core", 61.2)]


def _seed_analysis(org_id, owner_id, name):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.vendor_analysis import OptionsAnalysis, VendorOption

    app = create_app("testing")
    with app.app_context():
        cap = BusinessCapability(name="Finance operations " + name, organization_id=org_id)
        db.session.add(cap)
        db.session.flush()
        analysis = OptionsAnalysis(
            name=name, description="Pick the finance platform", capability_id=cap.id,
            created_by_id=owner_id, organization_id=org_id, status="completed",
            analysis_type="standard",
        )
        db.session.add(analysis)
        db.session.flush()
        for rank, (vendor, score) in enumerate(_VENDORS, start=1):
            db.session.add(VendorOption(
                analysis_id=analysis.id, organization_id=org_id, vendor_name=vendor,
                vendor_type="vendor_product", total_score=score, ranking=rank,
                cost_score=score - 5, capability_coverage_score=score - 3, risk_score=score - 8,
                strategic_fit_score=score - 1, implementation_score=score - 10,
            ))
        db.session.commit()
        analysis_id = analysis.id
        db.session.remove()
    return analysis_id


def _stored(analysis_id):
    from app import create_app, db
    from app.models.vendor_analysis import OptionsAnalysis

    app = create_app("testing")
    with app.app_context():
        a = db.session.get(OptionsAnalysis, analysis_id)
        out = (a.approval_status, a.approval_notes)
        db.session.remove()
    return out


def _role_of(email):
    from app import create_app, db
    from app.models import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        role = user.enterprise_role
        db.session.remove()
    return role


def _open_panel(page, heading):
    page.get_by_role("button", name=heading).first.click()


def test_architect_reviews_comparison_and_reviewer_records_decision(browser, live_server, seeded):
    ids = seeded["ids"]
    name = "ERP platform selection"
    analysis_id = _seed_analysis(ids["org"], ids["solution_architect_user"], name)
    detail = "%s/dashboard/vendor-analysis/%d" % (live_server, analysis_id)

    # --- the architect opens the analysis from the list and reads the comparison
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/dashboard/vendor-analysis", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=name, exact=True).click()
        page.wait_for_url("**/dashboard/vendor-analysis/%d" % analysis_id, timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name=name, level=1).wait_for(timeout=PAGE_TIMEOUT)

        page.get_by_role("button", name="Step 3 of 5", exact=False).click()
        table = page.locator("#comparison-table")
        table.wait_for(timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "document.querySelectorAll('#comparison-table thead th').length === 4", timeout=PAGE_TIMEOUT)
        head = table.locator("thead").inner_text()
        for vendor, _ in _VENDORS:
            assert vendor.upper() in head.upper()
        total_row = table.locator("tbody tr").first.inner_text()
        for _, score in _VENDORS:
            assert "%.1f" % score in total_row, total_row   # the stored figures, not a placeholder

        # --- adds a requirement; it is still there after a reload
        if not page.get_by_label("Requirement Name").is_visible():
            _open_panel(page, "Requirements Matrix")
        page.get_by_label("Requirement Name").fill("Single sign-on")
        page.get_by_role("button", name="Add", exact=True).click()
        page.get_by_text("Single sign-on").first.wait_for(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Step 3 of 5", exact=False).click()
        if not page.get_by_label("Requirement Name").is_visible():
            _open_panel(page, "Requirements Matrix")
        page.get_by_text("Single sign-on").first.wait_for(timeout=PAGE_TIMEOUT)
        assert "(1 requirement)" in page.get_by_role("button", name="Requirements Matrix").inner_text()

        # the architect owns the analysis, so cannot approve it herself: the Decision step
        # lists the ranking and nothing has been decided yet
        page.get_by_role("button", name="Step 4 of 5", exact=False).click()
        page.get_by_role("heading", name="Vendor Rankings").wait_for(timeout=PAGE_TIMEOUT)
        assert "Top Ranked" in page.locator("main, body").first.inner_text()
    finally:
        ctx.close()

    # --- a different person (organisation administrator) records the decision
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(detail, timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Step 4 of 5", exact=False).click()
        rankings = page.get_by_role("heading", name="Vendor Rankings")
        rankings.wait_for(timeout=PAGE_TIMEOUT)
        page.get_by_label("Selected Vendor").select_option(label="Northwind ERP (Score: 82.5)")
        page.get_by_label("Decision", exact=True).select_option("approved")
        page.get_by_label("Rationale").fill("Best coverage at the lowest risk")
        page.get_by_role("button", name="Submit Decision").first.click()
        page.locator("#confirm-dialog-vendor-decision").get_by_role("button", name="Submit Decision").click()
        page.get_by_text("Decision recorded successfully").wait_for(timeout=PAGE_TIMEOUT)
        assert _stored(analysis_id) == ("approved", "Best coverage at the lowest risk")

        # after a reload the analysis itself says it was approved
        page.reload(timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name=name, level=1).wait_for(timeout=PAGE_TIMEOUT)
        page.get_by_text("approved", exact=True).first.wait_for(timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()


def test_new_analysis_says_why_later_steps_are_locked(browser, live_server, seeded):
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/dashboard/vendor-analysis/new", timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name="New Vendor Options Analysis", level=1).wait_for(timeout=PAGE_TIMEOUT)
        # nothing chosen yet: the page says what to do rather than showing an empty box
        page.get_by_text(
            "Select capabilities, processes, or value streams above to discover matching vendors"
        ).wait_for(timeout=PAGE_TIMEOUT)
        for step in ("Step 2 of 5", "Step 3 of 5", "Step 4 of 5"):
            assert page.get_by_role("button", name=step, exact=False).is_disabled()
        # the vendor discovery button is not offered until something is chosen
        assert page.get_by_role("button", name="Discover Matching Vendors").is_disabled()
        # choosing a layer and a first capability enables it
        page.get_by_role("checkbox").first.check()
        assert page.get_by_role("button", name="Discover Matching Vendors").is_enabled()
        assert "1 capabilities selected" in page.locator("body").inner_text()
    finally:
        ctx.close()


def test_new_analysis_states_what_unlocks_each_locked_step(browser, live_server, seeded):
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        assert "solution_architect" in str(_role_of(seeded["emails"]["solution_architect"])).lower()
        page.goto(live_server + "/dashboard/vendor-analysis/new", timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name="New Vendor Options Analysis", level=1).wait_for(timeout=PAGE_TIMEOUT)
        notes = page.get_by_test_id("locked-steps")
        notes.wait_for(timeout=PAGE_TIMEOUT)
        text = notes.inner_text()
        assert "Scoring" in text and "name" in text and "capability" in text
        assert "Comparison" in text and "has been run" in text
        assert "Decision" in text and "ranked" in text
        assert "Export" in text and "created" in text
    finally:
        ctx.close()


def test_completed_analysis_shows_one_scored_view_with_plain_gaps(browser, live_server, seeded):
    ids = seeded["ids"]
    analysis_id = _seed_analysis(ids["org"], ids["solution_architect_user"], "Scored view check")
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto("%s/dashboard/vendor-analysis/%d" % (live_server, analysis_id), timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Step 3 of 5", exact=False).click()
        table = page.locator("#comparison-table")
        page.wait_for_function(
            "document.querySelectorAll('#comparison-table tbody tr').length > 0", timeout=PAGE_TIMEOUT)
        body = page.locator("main, body").first.inner_text()
        # scored analysis: no rubric of empty dashes, the scores are the view
        assert "Rate each vendor across risk" not in body
        assert "82.5" in table.inner_text()
        # costs never entered say so, and the page does not print N/A
        assert "Not recorded" in table.inner_text()
        assert "N/A" not in body
        assert page.get_by_test_id("cost-note").is_visible()
        # the rubric is still reachable on request
        page.get_by_role("button", name="Rate vendors by hand").click()
        page.get_by_text("Rate each vendor across risk").wait_for(timeout=PAGE_TIMEOUT)
    finally:
        ctx.close()
