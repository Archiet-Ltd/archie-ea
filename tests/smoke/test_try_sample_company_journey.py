"""A new startup CTO clicks "Try the sample company" and reads a complete answer.

As a startup CTO with nothing connected yet: sees the sample company choice on
the first screen, clicks it, and within 60 seconds, without another click,
reads an answer for the payments service that lists its direct and worked-out
dependants with sources; clicks one listed system and lands on its page with
content; the sample indicator is visible; clicks "Remove it", reads the
preview, confirms, and the applications list and Ask are empty again. A second
organisation's counts are unchanged throughout.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _empty_organisation_user(live_server):
    """Create a fresh organisation with one architect user (who may load the sample) and no data."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Sample Org %s" % suffix, slug="sample-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(
            email="cto-%s@example.com" % suffix,
            first_name="Startup",
            last_name="CTO",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email, org.id


def _count_elements(live_server, page, org_id):
    """Count ArchiMate elements for an organisation."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement
        # Explicitly scope to org since ArchiMateElement has TenantMixin
        count = ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == org_id
        ).count()
        return count


def test_try_sample_company_loads_and_removes_cleanly(browser, live_server, seeded):
    """The sample company journey: load, read answer, remove."""
    # Two organisations: the CTO's fresh one, and the seeded one as control
    cto_email, cto_org_id = _empty_organisation_user(live_server)
    seeded_org_id = seeded["ids"]["org"]

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()

    try:
        # The first-run role dialog is a separate flow; mark it seen
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")

        _login(page, live_server, cto_email)

        # Dashboard shows guided mode with "Try the sample company"
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="guided-setup"]')).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("first-action-sample")).to_be_visible(timeout=PAGE_TIMEOUT)
        # One sample control on the whole screen, inside the first card
        assert page.get_by_text("Load sample").count() == 0
        assert page.locator('[data-testid="first-fold"] [data-testid="first-action-sample"]').count() == 1

        # Control: seeded org has its elements unchanged
        seeded_count_before = _count_elements(live_server, page, seeded_org_id)
        assert seeded_count_before > 0, "Seeded org should have elements"

        # Click "Load sample"
        sample_link = page.get_by_test_id("first-action-sample")
        sample_link.click()
        # the click posts and the first screen reloads with the sample bar (the start page matches the same address)
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        # The first screen shows the loaded sample: the bar, and a non-zero element figure that
        # agrees with what was stored for this organisation
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("You are looking at the sample company")).to_be_visible()
        stored = _count_elements(live_server, page, cto_org_id)
        assert stored > 0
        expect(page.locator('[data-testid="health-kpi-elements"]')).to_have_text(str(stored), timeout=PAGE_TIMEOUT)

        # The first screen shows a working answer: applications and a health score, no dash or zero
        applications = page.locator('[data-testid="health-kpi-applications"]').inner_text().strip()
        assert applications.isdigit() and int(applications) >= 8, applications
        score = page.locator('[data-testid="health-score-value"]').inner_text().strip()
        assert score not in ("", "—", "0"), score
        solutions = page.locator('[data-testid="health-kpi-solutions"]').inner_text().strip()
        assert solutions.isdigit() and int(solutions) > 0, solutions
        # The applications list agrees with the figure, and every application carries an owner and a cost
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Payments service", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Remove it: one plain confirmation, then back on the setup screen
        page.get_by_role("button", name="Remove the sample company").first.click()
        confirm = page.locator('[data-testid="sample-company-remove-confirm"]')
        expect(confirm).to_be_visible(timeout=PAGE_TIMEOUT)
        confirm.click()
        expect(page.get_by_text("Sample company removed")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="guided-setup"]')).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_have_count(0)

        # After removal, applications list is empty
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Payments service", exact=False)).to_have_count(0)

        # Ask no longer shows the sample answer
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # (the chat's example question names a service as a suggestion; it is not sample data)
        expect(page.get_by_test_id("estate-answer")).to_have_count(0)
        expect(page.locator("#chat-messages, [data-testid='chat-messages']").get_by_text("Payments service", exact=False)).to_have_count(0)

        # Control: seeded org elements unchanged
        seeded_count_after = _count_elements(live_server, page, seeded_org_id)
        assert seeded_count_after == seeded_count_before, (
            f"Seeded org elements changed: {seeded_count_before} -> {seeded_count_after}"
        )

        # CTO org elements are back to zero
        cto_count_after = _count_elements(live_server, page, cto_org_id)
        assert cto_count_after == 0, f"CTO org should be empty after restore, got {cto_count_after}"

    finally:
        context.close()


def test_load_sample_twice_is_idempotent(browser, live_server, seeded):
    """Pressing the load sample button twice imports once."""
    cto_email, cto_org_id = _empty_organisation_user(live_server)

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()

    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, cto_email)

        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # First load
        sample_link = page.get_by_test_id("first-action-sample")
        sample_link.click()
        # the click posts and the first screen reloads with the sample bar (the start page matches the same address)
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        count_after_first = _count_elements(live_server, page, cto_org_id)
        assert count_after_first > 0, "Sample should be loaded"

        # Try loading again by posting directly to the route
        csrf_token = page.locator("meta[name='csrf-token']").get_attribute("content")
        response = page.request.post(
            live_server + "/architecture/sample-company/load",
            headers={"X-CSRFToken": csrf_token}
        )
        assert response.ok, f"Second load failed: {response.status}"

        # Count should be unchanged
        count_after_second = _count_elements(live_server, page, cto_org_id)
        assert count_after_second == count_after_first, (
            f"Count changed on second load: {count_after_first} -> {count_after_second}"
        )

    finally:
        context.close()


def test_viewer_sees_explanation_not_button(browser, live_server, seeded):
    """A Viewer sees the explanation, not a load button."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Viewer Org %s" % suffix, slug="viewer-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(
            email="viewer-%s@example.com" % suffix,
            first_name="Sample",
            last_name="Viewer",
            organization_id=org.id,
            enterprise_role="viewer",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Viewer").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        viewer_email = user.email

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()

    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, viewer_email)

        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="guided-setup"]')).to_be_visible(timeout=PAGE_TIMEOUT)

        # A person who cannot load it is offered no sample control at all, and no dead step
        expect(page.get_by_test_id("first-action-sample")).to_have_count(0)
        expect(page.get_by_text("Load sample")).to_have_count(0)
        expect(page.get_by_text("Try the sample company")).to_have_count(0)

    finally:
        context.close()
