"""A Compliance colleague opens Controls from the sidebar and reads it.

Goal: a person who holds the Compliance role, with an ordinary account, finds Controls
in the sidebar, clicks it, and lands on the Controls page with its heading and content
rather than a refusal.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import sign_in
from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_compliance_user_opens_controls_from_the_sidebar(browser, live_server, seeded):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _walk(page, live_server, seeded)
    finally:
        context.close()


def _ordinary_compliance_email(seeded):
    """A colleague with the Compliance persona and the ordinary "User" account role: not an administrator,
    and not the seeded "Architect" account role, which would pass every architect-only page."""
    import uuid

    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        template = User.query.filter_by(email=seeded["emails"]["compliance"]).one()
        email = "controls.compliance.%s@example.com" % uuid.uuid4().hex[:8]
        user = User(email=email, first_name="Cora", last_name="Compliance", organization_id=template.organization_id,
                    enterprise_role="compliance", confirmed=True)
        user.role = Role.query.filter_by(name="User").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        assert not user.is_org_admin and not user.is_platform_admin
        db.session.remove()
    return email


def _walk(page, live_server, seeded):
    sign_in(page, live_server, _ordinary_compliance_email(seeded))
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    sidebar = page.locator('[data-testid="sidebar"]')
    link = sidebar.locator('a[href="/policy-monitoring/"]').first
    # The Compliance group may be collapsed; open it the way a person would.
    if not link.is_visible():
        sidebar.get_by_text("Compliance", exact=True).first.click()
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    link.click()

    page.wait_for_url("**/policy-monitoring/", timeout=PAGE_TIMEOUT)
    body = page.locator("main")
    expect(body).to_be_visible(timeout=PAGE_TIMEOUT)
    text = body.inner_text()
    assert "Forbidden" not in text and "do not have permission" not in text.lower(), text[:300]
    assert page.locator("h1").first.inner_text().strip() == "Controls", "the page is not headed Controls"
    assert page.locator('[data-testid="policy-fetch-error"]').count() == 0, "Controls shows its read-error banner"
    assert len(text.strip()) > 80, "Controls opened with no content"
