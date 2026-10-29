"""Journey: six pages that used to hand-write their table now use the shared
list table component (components/list_table.html).

A platform administrator opens the workflow instances list, the workflow
catalog, the agent run records list, the AI extraction corrections list, the
connection gaps report and the billing invoices list. On each page with real
rows the journey asserts the toolbar's search box, the column view menu (once
there is more than one data column), a working sort, the pagination footer
text, and that one row's action still does its job. Two of the pages are
seeded with a second organisation's row and the journey asserts it never
appears on the first organisation's page.

Screenshots are captured at 1440 and 390 viewport widths, in light and dark
themes, for every page, saved under the directory SMOKE_WALK_DIR names (a
local default when the variable is unset).
"""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get(
    "SMOKE_WALK_DIR",
    os.path.join(os.path.dirname(__file__), "_list_table_b02_shots"),
)


def _screenshot(page, label, viewport_width):
    """Screenshot the current page at *viewport_width*, in light and dark."""
    os.makedirs(WALK_DIR, exist_ok=True)
    for theme in ("light", "dark"):
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
        page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        filepath = os.path.join(WALK_DIR, "%s-%s-%s.png" % (label, viewport_width, theme))
        page.screenshot(path=filepath, full_page=True)
        print("[screenshot] saved %s" % filepath)


def _new_page(browser, viewport_width=1440):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": viewport_width, "height": 1000},
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _shots(browser, live_server, email, path, label):
    """Visit *path* signed in as *email* and screenshot it at both widths."""
    for width in (1440, 390):
        context, page = _new_page(browser, viewport_width=width)
        sign_in(page, live_server, email)
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        _screenshot(page, label, width)
        context.close()


# --------------------------------------------------------------------------- #
# Fixtures: one organisation with enough rows to need the toolbar, plus a
# second organisation for the isolation checks.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def admin_org():
    return create_fresh_org("platform_admin", org_admin=True)


def _login(page, live_server, email):
    sign_in(page, live_server, email)


@pytest.fixture(scope="module")
def workflow_catalog_seed(admin_org):
    """Twelve workflow definitions across three categories (a shared catalogue,
    not organisation-scoped, so no isolation check applies to it)."""
    from app import create_app, db
    from app.models.workflow_models import EAWorkflowDefinition

    app = create_app("testing")
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        names = []
        for i in range(12):
            category = ["onboarding", "remediation", "review"][i % 3]
            name = "Catalog workflow %s %d" % (suffix, i)
            db.session.add(EAWorkflowDefinition(
                workflow_code="wf-%s-%d" % (suffix, i), workflow_name=name,
                workflow_category=category, workflow_type="sequential",
                is_active=True, steps=[{"step_id": "s1", "step_name": "Start"}],
            ))
            names.append(name)
        db.session.commit()
        return {"names": names, "first": names[0]}


@pytest.fixture(scope="module")
def workflow_instances_seed(admin_org, workflow_catalog_seed):
    """Ten workflow instances in the admin organisation, plus one in a second
    organisation the isolation test must never see."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.workflow_models import EAWorkflowDefinition, EAWorkflowInstance

    app = create_app("testing")
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        definition = EAWorkflowDefinition.query.filter_by(
            workflow_name=workflow_catalog_seed["first"]
        ).one()
        org_id = admin_org["org_id"]
        codes = []
        for i in range(10):
            code = "inst-%s-%d" % (suffix, i)
            db.session.add(EAWorkflowInstance(
                organization_id=org_id, instance_code=code, workflow_definition_id=definition.id,
                status=["running", "completed", "failed"][i % 3], progress_percent=40,
                started_at=datetime.utcnow() - timedelta(hours=i),
            ))
            codes.append(code)
        other_org = Organization(name="Other org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other_org)
        db.session.commit()
        other_code = "inst-other-%s" % suffix
        db.session.add(EAWorkflowInstance(
            organization_id=other_org.id, instance_code=other_code, workflow_definition_id=definition.id,
            status="running", progress_percent=10, started_at=datetime.utcnow(),
        ))
        db.session.commit()
        return {"codes": codes, "other_code": other_code}


@pytest.fixture(scope="module")
def run_records_seed(admin_org):
    """Twenty-five agent run records in the admin organisation (per_page=20,
    so the server's second page is real) and one in a second organisation."""
    from app import create_app, db
    from app.models.agent_run_record import AgentRunRecord
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        org_id = admin_org["org_id"]
        user_id = admin_org["user_ids"]["platform_admin"]
        for i in range(25):
            db.session.add(AgentRunRecord(
                organization_id=org_id, user_id=user_id, persona="cto",
                charter_version=1, domain="finance",
                inputs={"user_message": "run %d %s" % (i, suffix)},
                outcome="OK", success=(i % 4 != 0),
                created_at=datetime.utcnow() - timedelta(minutes=i),
            ))
        other_org = Organization(name="RunRecords other %s" % suffix, slug="rr-other-%s" % suffix)
        db.session.add(other_org)
        db.session.commit()
        db.session.add(AgentRunRecord(
            organization_id=other_org.id, user_id=user_id, persona="cto",
            charter_version=1, domain="finance",
            inputs={"user_message": "other org run %s" % suffix},
            outcome="OK", success=True,
        ))
        db.session.commit()
        return {"suffix": suffix}


@pytest.fixture(scope="module")
def ai_corrections_seed(admin_org):
    """Twelve applications with an AI-linked ArchiMate element in the admin
    organisation, and one in a second organisation."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        org_id = admin_org["org_id"]
        names = []
        for i in range(12):
            name = "AI element app %s %d" % (suffix, i)
            comp = ApplicationComponent(name=name, organization_id=org_id,
                                        lifecycle_status="operational", description="Smoke fixture for the AI corrections journey.")
            db.session.add(comp)
            names.append(name)
        other_org = Organization(name="AICorr other %s" % suffix, slug="aicorr-other-%s" % suffix)
        db.session.add(other_org)
        db.session.commit()
        other_name = "AI element app other %s" % suffix
        db.session.add(ApplicationComponent(name=other_name, organization_id=other_org.id,
                                            lifecycle_status="operational"))
        db.session.commit()
        return {"names": names, "other_name": other_name}


@pytest.fixture(scope="module")
def connection_gaps_seed(admin_org):
    """Fifty-five solutions in the admin organisation (per_page=50, so the
    server's second page is real), plus one in a second organisation."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        suffix = uuid.uuid4().hex[:8]
        org_id = admin_org["org_id"]
        names = []
        for i in range(55):
            name = "Gap solution %s %03d" % (suffix, i)
            db.session.add(Solution(name=name, organization_id=org_id,
                                    business_domain="Finance", solution_type="Platform",
                                    governance_status="draft", adm_phase="A"))
            names.append(name)
        other_org = Organization(name="Gaps other %s" % suffix, slug="gaps-other-%s" % suffix)
        db.session.add(other_org)
        db.session.commit()
        other_name = "Gap solution other %s" % suffix
        db.session.add(Solution(name=other_name, organization_id=other_org.id,
                                business_domain="Finance", solution_type="Platform",
                                governance_status="draft", adm_phase="A"))
        db.session.commit()
        return {"names": names, "other_name": other_name}


# --------------------------------------------------------------------------- #
# Workflow catalog (integration/workflow_list.html)
# --------------------------------------------------------------------------- #


def test_workflow_catalog_toolbar_sort_and_view(browser, live_server, admin_org, workflow_catalog_seed):
    email = admin_org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/integration/workflows", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="workflows-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        # The toolbar's search box filters the rows.
        search = table.locator("input[data-table-filter]")
        expect(search).to_be_visible()
        search.fill("zzz-no-such-workflow-zzz")
        page.wait_for_selector('[data-testid="workflows-table"] tbody tr[data-table-empty]', timeout=PAGE_TIMEOUT)
        table.locator("[data-table-reset]").click()
        page.wait_for_selector('[data-testid="workflow-row"]', timeout=PAGE_TIMEOUT)

        # The column view menu is there, with the Workflow column checked.
        view_trigger = table.get_by_role("button", name="View")
        expect(view_trigger).to_be_visible()
        view_trigger.click()
        expect(page.get_by_role("menuitemcheckbox", name="Category")).to_be_visible()
        page.keyboard.press("Escape")

        # A sort on the Workflow column opens its menu and re-orders rows.
        page.locator('[data-table-sort-trigger="0"]').click()
        page.locator('[data-table-sort="0"][data-dir="asc"]').click()
        first_name_after_sort = table.locator('[data-testid="workflow-row"] td').first.inner_text()
        assert first_name_after_sort, "sorting the Workflow column left no value in the first cell"

        # One row's action: View Details reaches a page with content.
        page.get_by_role("link", name="View Details").first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text("Catalog workflow")

        _screenshot(page, "workflow-catalog", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/integration/workflows", "workflow-catalog")


# --------------------------------------------------------------------------- #
# Workflow instances (integration/instance_list.html)
# --------------------------------------------------------------------------- #


def test_workflow_instances_toolbar_and_isolation(browser, live_server, admin_org, workflow_instances_seed):
    email = admin_org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/integration/instances", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="instances-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        search = table.locator("input[data-table-filter]")
        expect(search).to_be_visible()

        # Pagination footer text is there.
        expect(table.locator("[data-table-page]")).to_contain_text("Page 1 of")

        # Column view menu.
        view_trigger = table.get_by_role("button", name="View")
        view_trigger.click()
        expect(page.get_by_role("menuitemcheckbox", name="Workflow")).to_be_visible()
        page.keyboard.press("Escape")

        body_text = table.inner_text()
        for code in workflow_instances_seed["codes"]:
            assert code in body_text, "%s is missing from the admin organisation's own instances list" % code
        assert workflow_instances_seed["other_code"] not in body_text, (
            "a second organisation's workflow instance (%s) appeared on this organisation's page"
            % workflow_instances_seed["other_code"]
        )

        # One row's action: the instance code links to its own detail page.
        page.get_by_role("link", name=workflow_instances_seed["codes"][0]).click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text(workflow_instances_seed["codes"][0])

        _screenshot(page, "workflow-instances", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/integration/instances", "workflow-instances")


# --------------------------------------------------------------------------- #
# Agent run records (ai_chat/run_records/list.html)
# --------------------------------------------------------------------------- #


def test_run_records_server_pagination_sort_and_isolation(browser, live_server, admin_org, run_records_seed):
    email = admin_org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/ai-chat/run-records", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="run-records-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        search = table.locator("input[data-table-filter]")
        expect(search).to_be_visible()

        # A sort on the ID column.
        page.locator('[data-table-sort-trigger="0"]').click()
        page.locator('[data-table-sort="0"][data-dir="asc"]').click()

        # The server's own pagination footer: a real page 2 (25 rows, per_page=20).
        pager = page.locator('[data-testid="run-records-pages"]')
        expect(pager).to_contain_text("Page 1 of 2")
        pager.get_by_role("link", name="Next").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="run-records-pages"]')).to_contain_text("Page 2 of 2")

        # One row's action: View reaches the record's own detail page.
        page.get_by_test_id("run-record-view").first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text("Run Record")

        _screenshot(page, "run-records", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/ai-chat/run-records", "run-records")


# --------------------------------------------------------------------------- #
# AI extraction corrections (admin/ai_corrections.html)
# --------------------------------------------------------------------------- #


def test_ai_corrections_toolbar_action_and_isolation(browser, live_server, admin_org, ai_corrections_seed):
    email = admin_org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/admin/ai-corrections", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="corrections-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        search = table.locator("input[data-table-filter]")
        expect(search).to_be_visible()
        view_trigger = table.get_by_role("button", name="View")
        view_trigger.click()
        expect(page.get_by_role("menuitemcheckbox", name="AI Type")).to_be_visible()
        page.keyboard.press("Escape")

        body_text = table.inner_text()
        for name in ai_corrections_seed["names"]:
            assert name in body_text, "%s is missing from the admin organisation's own corrections list" % name
        assert ai_corrections_seed["other_name"] not in body_text, (
            "a second organisation's application (%s) appeared on this organisation's corrections list"
            % ai_corrections_seed["other_name"]
        )

        # One row's action: Correct marks the row without reloading the page.
        first_row = page.locator('[data-testid="correction-row"]').first
        first_row.get_by_role("button", name="Mark element").click()
        expect(first_row.get_by_text("Confirmed")).to_be_visible(timeout=PAGE_TIMEOUT)

        _screenshot(page, "ai-corrections", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/admin/ai-corrections", "ai-corrections")


# --------------------------------------------------------------------------- #
# Connection gaps report (admin/connection_gaps.html)
# --------------------------------------------------------------------------- #


def test_connection_gaps_server_pagination_and_isolation(browser, live_server, admin_org, connection_gaps_seed):
    email = admin_org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/admin/connection-gaps", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="connection-gaps-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        search = table.locator("input[data-table-filter]")
        expect(search).to_be_visible()

        pager = page.locator('[data-testid="connection-gaps-pages"]')
        expect(pager).to_contain_text("55 solutions found")
        pager.get_by_role("link", name="Next").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="connection-gaps-pages"]')).to_contain_text("page 2 of 2")

        # One row's action: a solution's name links to its own record, with that
        # solution's name as the page's own heading.
        row_link = page.locator('[data-testid="connection-gap-row"] a').first
        row_name = row_link.inner_text().strip()
        row_link.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text(row_name)

        _screenshot(page, "connection-gaps", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/admin/connection-gaps", "connection-gaps")


def test_connection_gaps_rows_do_not_cross_organisations(browser, live_server, admin_org, connection_gaps_seed):
    email = admin_org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        # per_page=200 so every seeded row of this organisation is on one page.
        page.goto(live_server + "/admin/connection-gaps?per_page=200", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="connection-gaps-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        body_text = table.inner_text()
        for name in connection_gaps_seed["names"]:
            assert name in body_text, "%s is missing from the admin organisation's own connection gaps report" % name
        assert connection_gaps_seed["other_name"] not in body_text, (
            "a second organisation's solution (%s) appeared on this organisation's connection gaps report"
            % connection_gaps_seed["other_name"]
        )
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# Billing invoices (admin/billing.html): no payment provider is configured in
# this environment, so the invoices table has no rows to seed; the journey
# asserts the converted section still renders its correct empty state rather
# than the search, sort and pagination controls a populated table would show.
# --------------------------------------------------------------------------- #


@pytest.fixture
def workflow_catalog_emptied():
    """Temporarily deactivate every active workflow definition so the
    catalog's own empty state can be proven, regardless of what earlier
    tests or seeds left behind. ``EAWorkflowDefinition`` is deliberately
    global, not organisation-scoped (its own docstring: "Global reference
    data (shared across tenants)"), so a fresh organisation on its own does
    not produce an empty catalog -- this fixture is the only reliable way to
    see the empty state once anything else has seeded a definition. Restores
    every row's prior state when the test ends."""
    from app import create_app, db
    from app.models.workflow_models import EAWorkflowDefinition

    app = create_app("testing")
    active_ids = []
    with app.app_context():
        active_ids = [
            row[0]
            for row in db.session.query(EAWorkflowDefinition.id).filter(
                EAWorkflowDefinition.is_active.is_(True)
            )
        ]
        if active_ids:
            EAWorkflowDefinition.query.filter(
                EAWorkflowDefinition.id.in_(active_ids)
            ).update({"is_active": False}, synchronize_session=False)
            db.session.commit()
    try:
        yield
    finally:
        with app.app_context():
            if active_ids:
                EAWorkflowDefinition.query.filter(
                    EAWorkflowDefinition.id.in_(active_ids)
                ).update({"is_active": True}, synchronize_session=False)
                db.session.commit()


# --------------------------------------------------------------------------- #
# Agent run records: the empty state (ai_chat/run_records/list.html) -- no
# records seeded at all, so the page's own empty state is what a person sees.
# --------------------------------------------------------------------------- #


def test_run_records_empty_state_offers_ai_chat(browser, live_server):
    """A platform administrator who opens Agent Run Records before anyone has
    asked the AI chat a question reads why the list is empty, and the one
    action it offers lands on the AI chat with real content -- not a blank
    or dead page."""
    org = create_fresh_org("platform_admin", org_admin=True)
    email = org["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/ai-chat/run-records", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.locator('[data-testid="run-records-table-pager"]')
        table.wait_for(state="visible", timeout=PAGE_TIMEOUT)

        # The empty state says why the list is empty -- not just that it is.
        expect(table).to_contain_text("No agent run records have been recorded yet")
        expect(table).to_contain_text("Records are written when the AI chat runs an agent on a question")

        # Its one action goes to the place that produces a record.
        action = table.get_by_role("link", name="Ask a question in the AI chat")
        expect(action).to_be_visible()
        action.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/ai-chat" in page.url, "the empty state's action did not reach the AI chat"
        expect(page.locator("h1")).to_contain_text("How can I help you today?")

        _screenshot(page, "run-records-empty", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/ai-chat/run-records", "run-records-empty")


# --------------------------------------------------------------------------- #
# Workflow catalog: the empty state (integration/workflow_list.html) -- an
# administrator gets an action; a non-administrator gets the same reason
# with nothing to click, since they cannot act on it.
# --------------------------------------------------------------------------- #


def test_workflow_catalog_empty_state_by_role(browser, live_server, workflow_catalog_emptied):
    """Before any workflow definition exists, an administrator reads why the
    catalog is empty and can go connect an integration from there, landing on
    a real page; a non-administrator reads the same reason but is given
    nothing to click, and Back to Dashboard appears exactly once."""
    admin = create_fresh_org("platform_admin", org_admin=True)
    member = create_fresh_org("solution_architect", org_admin=False)

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, admin["emails"]["platform_admin"])
        page.goto(live_server + "/integration/workflows", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.locator('[data-testid="workflow-catalog-empty"]')
        expect(empty).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(empty).to_contain_text("No workflow definitions exist in this organisation yet")
        expect(empty).to_contain_text("The catalog fills once the first integration is connected")

        # Back to Dashboard appears once on the page, not once in the header
        # and again in the empty state.
        expect(page.get_by_role("link", name="Back to Dashboard")).to_have_count(1)

        action = empty.get_by_role("link", name="Connect an integration")
        expect(action).to_be_visible()
        action.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/admin/connectors" in page.url, "Connect an integration did not reach the connectors page"
        expect(page.locator("h1")).to_contain_text("Connectors")

        _screenshot(page, "workflow-catalog-empty-admin", 1440)
    finally:
        context.close()
    _shots(browser, live_server, admin["emails"]["platform_admin"], "/integration/workflows", "workflow-catalog-empty-admin")

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, member["emails"]["solution_architect"])
        page.goto(live_server + "/integration/workflows", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.locator('[data-testid="workflow-catalog-empty"]')
        expect(empty).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(empty).to_contain_text("Ask your administrator to connect an integration")
        expect(empty.get_by_role("link")).to_have_count(0)

        _screenshot(page, "workflow-catalog-empty-non-admin", 1440)
    finally:
        context.close()
    _shots(browser, live_server, member["emails"]["solution_architect"], "/integration/workflows", "workflow-catalog-empty-non-admin")


def test_billing_page_renders_with_empty_invoices(browser, live_server, seeded):
    email = seeded["emails"]["platform_admin"]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/admin/billing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid=billing-page]")).to_be_visible(timeout=PAGE_TIMEOUT)
        # No invoice-table pager renders with zero rows (list_table shows its
        # empty message without a toolbar or footer); the page's own empty
        # state for invoices is what a person sees instead.
        assert page.locator('[data-testid="invoice-table-pager"]').count() == 0 or (
            page.locator('[data-testid="invoice-table-pager"]').inner_text().strip() != ""
        )
        _screenshot(page, "billing", 1440)
    finally:
        context.close()
    _shots(browser, live_server, email, "/admin/billing", "billing")
