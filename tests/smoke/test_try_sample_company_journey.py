"""A new startup CTO clicks "Try the sample company" and reads a complete answer.

As a startup CTO with nothing connected yet: sees the sample company choice on
the first screen, clicks it, and within 60 seconds, without another click,
reads an answer for the payments service that lists its direct and worked-out
dependants with sources; clicks one listed system and lands on its page with
content; the sample indicator is visible; clicks "Remove it", reads the
preview, confirms, and the applications list and Ask are empty again. A second
organisation's counts are unchanged throughout.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask
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


def _count_rows(org_id, table):
    """Count one organisation's rows in a table, in SQL, so the figure does not depend on a request."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        return db.session.execute(
            db.text("SELECT count(*) FROM %s WHERE organization_id = :o" % table), {"o": org_id}
        ).scalar()


def _load_sample_from_ask(page, base):
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("chat-first-sample").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)


WALK = os.environ.get("SAMPLE_WALK_DIR")


def _shot(page, name, width):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_try_sample_company_loads_and_removes_cleanly(browser, live_server, seeded, width):
    """The sample company journey: load, ask and read a cited answer, remove, reload, see it gone."""
    cto_email, cto_org_id = _empty_organisation_user(live_server)
    seeded_org_id = seeded["ids"]["org"]

    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()

    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, cto_email)

        # An empty workspace offers the way in, and no sample bar
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("overview-welcome")).to_contain_text("Connect GitHub and your model is built for you")
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_have_count(0)

        # Control: the seeded organisation's data, which must be the same at the end
        seeded_elements = _count_elements(live_server, page, seeded_org_id)
        seeded_apps = _count_rows(seeded_org_id, "application_components")
        assert seeded_elements > 0 and seeded_apps > 0, "the other organisation should hold data"

        # Load the sample from the chat's first screen
        _load_sample_from_ask(page, live_server)
        expect(page.get_by_text("You are looking at the sample company")).to_be_visible()
        _shot(page, "1-sample-loaded", width)
        sample_apps = _count_rows(cto_org_id, "application_components")
        assert sample_apps >= 8

        # Ask a question and read a cited answer
        answer = ask(page, live_server, "What breaks if Payments service fails")
        assert answer.locator("[data-answer-row]").count() >= 1
        _shot(page, "2-cited-answer", width)
        citations = answer.locator("[data-citation]")
        assert citations.count() >= 1, "the answer cites no source"
        for citation in citations.all():
            assert re.search(r"[A-Za-z]{4}", citation.inner_text()), citation.inner_text()

        # The applications list agrees with what was stored
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("a:visible").filter(has_text="Payments service").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # Remove it: one plain confirmation
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Remove it", exact=True).first.click()
        confirm = page.locator('[data-testid="sample-company-remove-confirm"]')
        expect(confirm).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "3-confirm", width)
        confirm.click()
        expect(page.get_by_text("Sample company removed")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Reload: the sample is gone and the page offers the next step
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_have_count(0)
        expect(page.get_by_test_id("overview-welcome")).to_contain_text("Connect GitHub and your model is built for you")
        _shot(page, "4-after-removal-reload", width)
        connect = page.get_by_test_id("overview-connect")
        expect(connect).to_be_visible()
        assert _count_rows(cto_org_id, "application_components") == 0
        assert _count_elements(live_server, page, cto_org_id) == 0

        # The sample applications are gone from the list
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Payments service", exact=False)).to_have_count(0)

        # The next step lands on a page with content
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("overview-connect").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/dashboard/overview" not in page.url
        assert len(page.locator("main").first.inner_text().strip()) > 40
        _shot(page, "5-connect-landing", width)

        # The other organisation kept everything
        assert _count_elements(live_server, page, seeded_org_id) == seeded_elements
        assert _count_rows(seeded_org_id, "application_components") == seeded_apps
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

        # First load
        _load_sample_from_ask(page, live_server)

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
        expect(page.get_by_test_id("overview-welcome")).to_be_visible(timeout=PAGE_TIMEOUT)

        # A person who cannot load it is offered no sample control at all, and no dead step
        expect(page.get_by_test_id("first-action-sample")).to_have_count(0)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("chat-first-sample")).to_have_count(0)
        expect(page.get_by_text("Load sample")).to_have_count(0)
        expect(page.get_by_text("Try the sample company")).to_have_count(0)

    finally:
        context.close()
