"""Journey tests for organisation administrator contracts access.

An organisation's Administrator may read its own organisation's contracts.
A reader without cost rights is neither offered the link nor allowed the page.
An Administrator of organisation B gets 404 or 403 on organisation A's contract.
"""

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, PAGE_TIMEOUT


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _create_org_admin(app, org_id, email, suffix):
    """Create a user with is_org_admin=True for the given organisation."""
    from app.models.user import Role, User

    with app.app_context():
        administrator_role = Role.query.filter_by(name="Administrator").one()
        user = User(
            email=email,
            first_name="Org",
            last_name="Admin",
            organization_id=org_id,
            enterprise_role="solution_architect",  # a regular persona, not procurement/cto
            confirmed=True,
        )
        user.role = administrator_role  # this makes is_admin() True
        user.password = PASSWORD
        # is_org_admin is a derived property that checks is_admin() for own org
        # Setting role to Administrator makes is_org_admin True for own org
        return user


@pytest.mark.smoke
@pytest.mark.journey
def test_org_admin_can_access_own_contracts(browser, live_server, seeded, app):
    """An organisation administrator can open the contracts list and a contract page."""
    from app import db
    from app.models.user import User
    from app.models.application_portfolio import VendorContract
    import datetime

    # Create an org admin user (not procurement, not cto, not portfolio_manager)
    org_id = seeded["ids"]["org"]
    admin_email = f"orgadmin.{seeded['emails']['procurement'].split('@')[0].split('.')[-1]}@example.com"

    with app.app_context():
        admin_user = _create_org_admin(app, org_id, admin_email, "admin")
        db.session.add(admin_user)
        db.session.commit()
        admin_id = admin_user.id

        # Create a contract for this organisation
        contract = VendorContract(
            organization_id=org_id,
            contract_name="Admin Test Contract",
            contract_number="ADMIN-TEST-001",
            status="active",
            contract_value=50000,
            start_date=datetime.date.today(),
        )
        db.session.add(contract)
        db.session.commit()
        contract_id = contract.id

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, admin_email)

        # ---- Contracts list page ----
        list_url = live_server + "/procurement/contracts"
        page.goto(list_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Should be allowed (not 403)
        expect(page.locator("body")).to_be_visible(timeout=PAGE_TIMEOUT)
        # The contract should be visible
        expect(page.get_by_text("Admin Test Contract", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Contract detail page ----
        detail_url = live_server + f"/procurement/contracts/{contract_id}"
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Admin Test Contract", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("ADMIN-TEST-001", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Sidebar should show the contracts link ----
        # Navigate to a page with sidebar and check the Spend group
        page.goto(live_server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # The Spend group should be visible and expanded or expandable
        spend_button = page.locator('[data-nav-key="spend"]').first
        expect(spend_button).to_be_visible(timeout=PAGE_TIMEOUT)
        # Click to expand if needed
        if spend_button.get_attribute("aria-expanded") == "false":
            spend_button.click()
        # The Contracts and licences link should be visible
        contracts_link = page.locator('[data-nav-key="contracts"]').first
        expect(contracts_link).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
        # Cleanup
        with app.app_context():
            from app.models.audit_log import AuditLog
            from sqlalchemy import text
            # Use raw SQL to avoid ORM autoflush issues
            db.session.execute(text("DELETE FROM soc2_audit_log WHERE user_id = :uid"), {"uid": admin_id})
            db.session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": admin_id})
            db.session.execute(text("DELETE FROM vendor_contracts WHERE id = :cid"), {"cid": contract_id})
            db.session.commit()


@pytest.mark.smoke
@pytest.mark.journey
def test_reader_without_cost_rights_denied_contracts(browser, live_server, seeded, app):
    """A reader without cost rights (e.g., solution_architect) is neither offered the contracts link nor allowed the page."""
    from app import db
    from app.models.user import User, Role
    from app.models.application_portfolio import VendorContract
    import datetime

    org_id = seeded["ids"]["org"]

    # Create a user with a persona that has no cost visibility (solution_architect)
    # and is NOT an org admin
    with app.app_context():
        architect_role = Role.query.filter_by(name="Architect").one()
        reader_email = f"reader.{seeded['emails']['procurement'].split('@')[0].split('.')[-1]}@example.com"
        reader_user = User(
            email=reader_email,
            first_name="Reader",
            last_name="NoCost",
            organization_id=org_id,
            enterprise_role="solution_architect",
            confirmed=True,
        )
        reader_user.role = architect_role  # NOT Administrator
        reader_user.password = PASSWORD
        db.session.add(reader_user)
        db.session.commit()
        reader_id = reader_user.id

        # Create a contract for this organisation
        contract = VendorContract(
            organization_id=org_id,
            contract_name="Reader Test Contract",
            contract_number="READER-TEST-001",
            status="active",
            contract_value=50000,
            start_date=datetime.date.today(),
        )
        db.session.add(contract)
        db.session.commit()
        contract_id = contract.id

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, reader_email)

        # ---- Contracts list page should be denied (403) ----
        list_url = live_server + "/procurement/contracts"
        response = page.goto(list_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 403, f"Expected 403 for reader without cost rights, got {response.status}"

        # ---- Contract detail page should be denied (403) ----
        detail_url = live_server + f"/procurement/contracts/{contract_id}"
        response = page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 403, f"Expected 403 for reader without cost rights, got {response.status}"

        # ---- Sidebar should NOT show the contracts link ----
        page.goto(live_server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # The Spend group should be visible
        spend_button = page.locator('[data-nav-key="spend"]').first
        expect(spend_button).to_be_visible(timeout=PAGE_TIMEOUT)
        # Click to expand if needed
        if spend_button.get_attribute("aria-expanded") == "false":
            spend_button.click()
        # The Contracts and licences link should NOT be visible
        contracts_link = page.locator('[data-nav-key="contracts"]').first
        expect(contracts_link).to_have_count(0)
    finally:
        context.close()
        # Cleanup
        with app.app_context():
            from sqlalchemy import text
            db.session.execute(text("DELETE FROM soc2_audit_log WHERE user_id = :uid"), {"uid": reader_id})
            db.session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": reader_id})
            db.session.execute(text("DELETE FROM vendor_contracts WHERE id = :cid"), {"cid": contract_id})
            db.session.commit()


@pytest.mark.smoke
@pytest.mark.journey
def test_org_admin_tenant_isolation(browser, live_server, seeded, app):
    """An Administrator of organisation B gets 404 or 403 on organisation A's contract."""
    from app import db
    from app.models.user import User, Role
    from app.models.organization import Organization
    from app.models.application_portfolio import VendorContract
    import datetime
    import uuid

    # Create a second organisation
    with app.app_context():
        org_b = Organization(
            name=f"Org B {uuid.uuid4().hex[:8]}",
            slug=f"org-b-{uuid.uuid4().hex[:8]}"
        )
        db.session.add(org_b)
        db.session.flush()

        # Create org admin for org B
        administrator_role = Role.query.filter_by(name="Administrator").one()
        admin_b_email = f"adminb.{uuid.uuid4().hex[:8]}@example.com"
        admin_b = User(
            email=admin_b_email,
            first_name="OrgB",
            last_name="Admin",
            organization_id=org_b.id,
            enterprise_role="solution_architect",
            confirmed=True,
        )
        admin_b.role = administrator_role
        admin_b.password = PASSWORD
        db.session.add(admin_b)
        db.session.flush()

        # Create a contract in org A (the seeded org)
        org_a_id = seeded["ids"]["org"]
        contract_a = VendorContract(
            organization_id=org_a_id,
            contract_name="Org A Contract",
            contract_number="ORGA-001",
            status="active",
            contract_value=100000,
            start_date=datetime.date.today(),
        )
        db.session.add(contract_a)
        db.session.commit()

        org_b_id = org_b.id
        admin_b_id = admin_b.id
        contract_a_id = contract_a.id

    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, admin_b_email)

        # ---- Try to access org A's contracts list ----
        list_url = live_server + "/procurement/contracts"
        response = page.goto(list_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Should see org B's contracts (empty), not org A's
        # The page should load (200) but show no contracts from org A
        assert response.status == 200, f"Expected 200 for own org contracts list, got {response.status}"
        # Should NOT see org A's contract
        expect(page.get_by_text("Org A Contract", exact=False)).to_have_count(0)

        # ---- Try to access org A's contract detail directly ----
        detail_url = live_server + f"/procurement/contracts/{contract_a_id}"
        response = page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Should get 404 (not found in org B) or 403
        assert response.status in (403, 404), f"Expected 403 or 404 for cross-org contract access, got {response.status}"
    finally:
        context.close()
        # Cleanup
        with app.app_context():
            from sqlalchemy import text
            db.session.execute(text("DELETE FROM soc2_audit_log WHERE user_id = :uid"), {"uid": admin_b_id})
            db.session.execute(text("DELETE FROM soc2_audit_log WHERE organization_id = :oid"), {"oid": org_b_id})
            db.session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": admin_b_id})
            db.session.execute(text("DELETE FROM organizations WHERE id = :oid"), {"oid": org_b_id})
            db.session.execute(text("DELETE FROM vendor_contracts WHERE id = :cid"), {"cid": contract_a_id})
            db.session.commit()