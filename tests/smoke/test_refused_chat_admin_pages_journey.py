"""A person without administrator rights who opens an AI chat administration page is told so, with a way back.

The person: an invited Viewer. The goal: understand why the page is not theirs and get back to work. The
three addresses (analytics, prompts, run records) each show the product's standard refusal page with its
heading, and its one link goes to the dashboard, which has content.

Set ONB_WALK_DIR to save screenshots.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")
NO_ACCESS = "You don't have access to this page"
PATHS = ["/ai-chat/admin/analytics", "/ai-chat/admin/prompts", "/ai-chat/run-records"]
SIZES = [
    ("1440-light", {"width": 1440, "height": 900}, "light"),
    ("390-dark", {"width": 390, "height": 844}, "dark"),
]


@pytest.fixture(scope="module")
def viewer_email(live_server, seeded):
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        email = "chatadmin.viewer.%s@example.com" % uuid.uuid4().hex[:8]
        user = User(email=email, first_name="Chat", last_name="Viewer",
                    organization_id=seeded["ids"]["org"], enterprise_role="viewer", confirmed=True)
        user.role = Role.query.filter_by(name="Viewer").first()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


@pytest.mark.parametrize("size,viewport,scheme", SIZES, ids=[s[0] for s in SIZES])
def test_a_refused_chat_administration_page_has_a_heading_and_a_way_back(
    browser, live_server, viewer_email, size, viewport, scheme
):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded")
        page.fill("#email", viewer_email)
        page.fill("#password", PASSWORD)
        page.locator("#submit").dispatch_event("click")
        page.wait_for_url(lambda u: "/account/login" not in u)
        for path in PATHS:
            assert page.goto(live_server + path, wait_until="domcontentloaded").status == 403, path
            assert NO_ACCESS in page.inner_text("body"), (path, page.inner_text("body")[:300])
            expect(page.get_by_role("heading", name=NO_ACCESS)).to_have_count(1)
            assert "Access forbidden" not in page.inner_text("body")
            if WALK:
                os.makedirs(WALK, exist_ok=True)
                page.screenshot(path=os.path.join(
                    WALK, "refused-%s-%s.png" % (path.strip("/").replace("/", "-"), size)), full_page=True)
            back = page.get_by_role("link", name="Go to the dashboard")
            expect(back).to_have_count(1)
            back.click()
            page.wait_for_url("**/dashboard/**")
            assert page.locator("main").inner_text().strip()
    finally:
        context.close()
