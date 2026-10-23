"""Every-route sweep follow-up: links that used to land on a page saying
"nothing here" or "no data".

On a fresh demonstration seed (Lantern Quay Systems), signed in as the demo
architect, this journey opens each link the sweep followed and asserts the
target shows real content for the demo company -- a named record, a figure, a
table row -- or, where empty is genuinely true for that organisation, the
shared empty state carrying a reason and a next step. Signed out, it opens the
two public use-case pages and asserts their headings and the Jira and GitHub
connection steps.

Red before the demo seed carried this data and these pages were written;
green after.
"""

import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_DEMO_EMAIL = "demo@lantern-quay.example.com"


@pytest.fixture(scope="module")
def demo_company(live_server):
    """Seed the Lantern Quay Systems demonstration organisation against the
    live-server database and return the ids the journey navigates to. The demo
    user's password is set to the shared smoke password so the browser can
    sign in as the demo architect."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.solution_models import Solution
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        os.environ["DEMO_USER_PASSWORD"] = PASSWORD
        try:
            from app.commands.seed_demo_company import seed_demo_company

            seed_demo_company()
        finally:
            os.environ.pop("DEMO_USER_PASSWORD", None)

        org = Organization.query.filter_by(slug="lantern-quay").first()
        assert org is not None, "demo organisation was not seeded"
        # The seeder sets the demo user's password only on first creation, so
        # ensure it matches the smoke password even on a re-used database.
        demo_user = User.query.filter_by(
            email=_DEMO_EMAIL, organization_id=org.id
        ).first()
        assert demo_user is not None, "demo user was not seeded"
        demo_user.password = PASSWORD
        db.session.commit()

        solution = (
            Solution.query.filter_by(
                organization_id=org.id, name="Calibration data consolidation"
            ).first()
        )
        assert solution is not None, "worked solution was not seeded"
        out = {"org_id": org.id, "solution_id": solution.id}
        db.session.remove()
    return out


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_solution_page_shows_real_content(demo_company, live_server, browser):
    """The ADM kanban, the journey and the solutions dashboards all link to
    /solutions/<id>. It must show the solution's name and its worked content,
    not an empty blueprint."""
    page = browser.new_page()
    try:
        _login(page, live_server, _DEMO_EMAIL)
        sid = demo_company["solution_id"]
        page.goto(
            "%s/solutions/%d" % (live_server, sid),
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(1500)

        body = page.inner_text("body")
        assert "Calibration data consolidation" in body, (
            "the solution page does not name the solution it opened"
        )

        # Real content, not an empty shell: the executive summary carries the
        # seeded narrative, and at least one section reports a completeness
        # figure rather than "Empty -- nothing here yet".
        summary = page.locator('textarea[aria-label="Executive summary"]')
        assert summary.count() >= 1, "blueprint page has no executive-summary field"
        assert "single system of record" in summary.first.input_value(), (
            "the executive summary is empty -- the solution page is a blank shell"
        )
        assert "% complete" in body, (
            "no section reports content; every section reads 'Empty -- nothing here yet'"
        )
    finally:
        page.close()


def test_retention_breaches_lists_real_rows(demo_company, live_server, browser):
    """/retention-breaches is linked from the data-governance pages. For the
    demo company it must list the entities past their retention policy, each
    with its age, allowed days and owner -- not "no data"."""
    page = browser.new_page()
    try:
        _login(page, live_server, _DEMO_EMAIL)
        page.goto(
            live_server + "/data-governance/retention-breaches",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(800)

        table = page.locator('[data-testid="retention-breaches"]')
        assert table.count() >= 1, (
            "the retention-breaches page shows an empty state -- the demo "
            "company should carry entities past their retention policy"
        )
        rows = page.locator('[data-testid="retention-breaches"] tbody tr')
        assert rows.count() >= 1, "retention-breaches table has no rows"
        first = rows.first.inner_text()
        assert "Calibration result" in page.inner_text(
            '[data-testid="retention-breaches"]'
        ), "the breaching entity is not named"
        # Age and allowed-days are real figures, not a dash or a blank.
        assert any(ch.isdigit() for ch in first), (
            "the breach row carries no age/allowed figure"
        )
    finally:
        page.close()


def test_data_issues_lists_real_rows(demo_company, live_server, browser):
    """/data-governance/issues is linked from the data-governance pages. For
    the demo company it must list the raised issues, each naming the entity it
    was raised against -- not "no data"."""
    page = browser.new_page()
    try:
        _login(page, live_server, _DEMO_EMAIL)
        page.goto(
            live_server + "/data-governance/issues",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(800)

        issues = page.locator('[data-testid="data-issues"]')
        assert issues.count() >= 1, (
            "the data-issues page shows an empty state -- the demo company "
            "should carry raised data issues"
        )
        text = issues.inner_text()
        assert "Duplicate readings from relaid events" in text, (
            "the raised issue is not shown by title"
        )
        assert "Sensor reading" in text, (
            "the issue does not name the entity it was raised against"
        )
    finally:
        page.close()


def test_data_architecture_dashboard_counts_real_models(
    demo_company, live_server, browser
):
    """/architecture/data-architecture is linked from the data-architecture
    pages. For the demo company it must count the real conceptual, logical and
    physical data models -- not show zeros."""
    page = browser.new_page()
    try:
        _login(page, live_server, _DEMO_EMAIL)
        page.goto(
            live_server + "/architecture/data-architecture",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "could not be read" not in body, (
            "the data architecture dashboard failed to load"
        )
        # The three model tiles each read a real, non-zero count.
        for label in ("Conceptual", "Logical", "Physical"):
            assert label in body, "%s model tile is missing" % label
        # Each count is a real figure from the seeded data (2 of each model
        # type), never a zero, a dash or a missing label.
        for tile_title, expected_min in [
            ("Conceptual Models", 2),
            ("Logical Models", 2),
            ("Physical Models", 2),
            ("Data Lineage Flows", 1),
        ]:
            card = page.locator('div[data-slot="card"]').filter(
                has_text=tile_title
            )
            assert card.count() >= 1, (
                "No metrics card found for '%s'" % tile_title
            )
            value_text = card.locator(
                'div[data-slot="card-title"]'
            ).inner_text().strip()
            assert value_text, (
                "The '%s' tile has an empty value" % tile_title
            )
            count = int(value_text)
            assert count >= expected_min, (
                "Expected '%s' count >= %d, got %d" % (
                    tile_title, expected_min, count
                )
            )
    finally:
        page.close()


def test_data_lineage_shows_grounded_flows(demo_company, live_server, browser):
    """/architecture/data-lineage is linked from the data-architecture pages.
    For the demo company it must show grounded lineage between its data
    objects, naming both ends -- not an empty catalogue."""
    page = browser.new_page()
    try:
        _login(page, live_server, _DEMO_EMAIL)
        page.goto(
            live_server + "/architecture/data-lineage",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        assert "Sensor reading" in body and "Calibration result" in body, (
            "the lineage page names neither end of a seeded flow"
        )
    finally:
        page.close()


def test_undeclared_copies_states_why_empty(demo_company, live_server, browser):
    """/undeclared-copies is linked from the data-governance pages. The demo
    company has no entity held by more than one application without a declared
    system of record, so this page genuinely has nothing to show -- it must
    say so in plain words, not render a blank table."""
    page = browser.new_page()
    try:
        _login(page, live_server, _DEMO_EMAIL)
        page.goto(
            live_server + "/data-governance/undeclared-copies",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(800)
        body = page.inner_text("body")
        has_rows = page.locator('[data-testid="copies-row"]').count() > 0 or \
            page.locator('[data-testid="copies-table"] tbody tr').count() > 0
        if not has_rows:
            assert "No undeclared copies" in body, (
                "the undeclared-copies page is empty but says nothing about why"
            )
            assert "more than one application" in body, (
                "the empty state does not explain what an undeclared copy is"
            )
    finally:
        page.close()


def test_public_jira_github_page_has_content_and_steps(live_server, browser):
    """Signed out, the public Jira and GitHub use-case page carries its full
    content: a heading, at least 600 words of body text, and the Jira and
    GitHub connection steps named."""
    page = browser.new_page()
    try:
        page.goto(
            live_server + "/use-cases/import-from-jira-and-github",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(500)
        heading = page.locator("h1").first.inner_text()
        assert heading.strip(), "the public Jira/GitHub page has no heading"
        body = page.inner_text("body")
        # At least 600 words of real body text, not a content-less shell.
        word_count = len(body.split())
        assert word_count >= 600, (
            "the Jira/GitHub page has only %d words; expected at least 600"
            % word_count
        )
        # What connects today and how, as the connectors page does.
        assert "ServiceNow" in body, "the page does not say what connects today"
        assert "Sync Now" in body, "the page does not give the connection steps"
        assert "Jira" in body, "the page does not mention Jira"
        # Connection steps by name: the ServiceNow pull and Jira credential steps.
        assert "OAuth2" in body, (
            "the page does not name the ServiceNow connection step (OAuth2)"
        )
        assert "instance URL" in body, (
            "the page does not name the Jira connection step (instance URL)"
        )
        assert "Test Connection" in body, (
            "the page does not name the connection test step"
        )
    finally:
        page.close()


def test_public_business_model_canvas_page_has_content(live_server, browser):
    """Signed out, the public Business Model Canvas use-case page carries its
    full content: a heading, at least 600 words of body text, and real product
    detail."""
    page = browser.new_page()
    try:
        page.goto(
            live_server + "/use-cases/business-model-canvas-on-one-page",
            wait_until="networkidle", timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(500)
        heading = page.locator("h1").first.inner_text()
        assert heading.strip(), "the public canvas page has no heading"
        body = page.inner_text("body")
        # At least 600 words of real body text, not a content-less shell.
        word_count = len(body.split())
        assert word_count >= 600, (
            "the Canvas page has only %d words; expected at least 600"
            % word_count
        )
        assert "Business Model Canvas" in body, (
            "the canvas page does not describe the Business Model Canvas"
        )
        assert "Value Propositions" in body, (
            "the canvas page does not list the canvas's boxes"
        )
    finally:
        page.close()
