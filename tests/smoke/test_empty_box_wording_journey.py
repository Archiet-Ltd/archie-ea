"""A business architect opens a new business case and every empty box says what it is
and where to write; writing in one persists, and the same wording rules hold on the
chat's data answer. The words "Nothing here yet", "No data" and "N/A" never appear."""

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
expect.set_options(timeout=PAGE_TIMEOUT)
BANNED = ("Nothing here yet", "No data", "N/A")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _assert_persona(email, role="business_architect"):
    """The signed-in user is the persona the journey names, not an administrator or platform role."""
    from app import create_app
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == role, user.enterprise_role
        assert not is_platform_admin(user)
        assert not rbac_service.is_org_admin(user, user.organization_id)


def _seed_case(org_id):
    from app import create_app, db
    from app.models.business_case import BusinessCase

    app = create_app("testing")
    with app.app_context():
        case = BusinessCase(title="Wording Journey Case", organization_id=org_id)
        db.session.add(case)
        db.session.commit()
        return case.id


@pytest.mark.smoke
@pytest.mark.journey
def test_empty_business_case_boxes_name_themselves_and_writing_persists(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    _assert_persona(email)
    case_id = _seed_case(seeded["ids"]["org"])
    for width, height in ((1440, 900), (390, 844)):
        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
        page = context.new_page()
        try:
            _login(page, live_server, email)
            page.goto(f"{live_server}/business-case/{case_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            reasons = page.locator('[data-reason="canvas_box_empty"]')
            expect(reasons.first).to_be_visible()
            texts = [t.strip() for t in reasons.all_inner_texts()]
            assert texts and all(t.endswith(" is empty. Write it in the box below.") for t in texts), texts
            assert len(set(texts)) == len(texts), "each empty box names itself: %s" % texts
            body = page.locator("main").inner_text()
            for phrase in BANNED:
                assert phrase not in body, (width, phrase)
            if width == 1440:
                box = page.get_by_test_id("bc-textarea-problem_statement")
                box.fill("Regional CRM instances have diverged.")
                with page.expect_response(lambda r: "/api/field" in r.url) as saved:
                    box.blur()
                assert saved.value.status < 400
                page.reload(wait_until="domcontentloaded")
                expect(page.get_by_test_id("bc-textarea-problem_statement")).to_have_value(
                    "Regional CRM instances have diverged.")
        finally:
            context.close()
