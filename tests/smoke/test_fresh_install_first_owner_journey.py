"""A first owner signs up on a database built from migrations alone and gets in.

The person: the first self-serve owner of a new install, a restore drill or a new environment,
where nothing has run after the schema step. The goal: sign up, land on the welcome page with its
steps in front of them, and open the AI chat. The database is built from empty by the migrations
and the sign-up form is the only thing that touches it. Before roles were part of the migrations,
the owner signed up with no role and the welcome page answered 403.

At 1440 and at 390 wide. Set FRESH_INSTALL_WALK_DIR to save screenshots.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine, text

from app.models.user import DEFAULT_ROLES
from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.test_schema_migrations import (  # noqa: F401  (fixture re-exported)
    _flask,
    scratch_databases,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("FRESH_INSTALL_WALK_DIR")
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]


def _count(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar_one()
    finally:
        engine.dispose()


def _shot(page, name, size):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s.png" % (name, size)), full_page=True)


def test_first_owner_on_a_database_built_from_migrations_reaches_welcome_and_the_chat(
    request, app, browser, scratch_databases, monkeypatch
):
    url = scratch_databases("fresh_install")
    _flask(url, ["schema-upgrade"])
    assert _count(url, "SELECT count(*) FROM users") == 0

    monkeypatch.setenv("TEST_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    server = boot_live_server(request, None, app)

    for size, viewport in SIZES:
        email = "owner.%s@example.com" % uuid.uuid4().hex[:8]
        context = browser.new_context(viewport=viewport)
        context.set_default_timeout(PAGE_TIMEOUT)
        context.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        try:
            page.goto(server + "/account/register", wait_until="domcontentloaded")
            page.fill("#first_name", "Casey")
            page.fill("#last_name", "Founder")
            page.fill("#email", email)
            page.fill("#password", PASSWORD)
            page.fill("#password2", PASSWORD)
            page.locator("#submit").click()
            page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)

            # The owner holds one of the roles the migrations created, so the welcome page shows its steps.
            assert _count(
                url,
                "SELECT r.name FROM users u JOIN roles r ON r.id = u.role_id "
                "WHERE u.email = '%s'" % email,
            ) in DEFAULT_ROLES
            reply = page.goto(server + "/account/welcome", wait_until="domcontentloaded")
            assert reply.status == 200, reply.status
            expect(page.get_by_role("navigation", name="Progress steps")).to_contain_text("Your sources")
            assert "Your sources" in page.locator("main").inner_text()
            _shot(page, "welcome", size)

            reply = page.goto(server + "/ai-chat", wait_until="domcontentloaded")
            assert reply.status == 200, reply.status
            assert re.search(r"/ai-chat", page.url), page.url
            assert page.locator("main").inner_text().strip()
            expect(page.locator("main textarea, main [contenteditable='true']").first).to_be_visible()
            _shot(page, "chat", size)
        finally:
            context.close()
