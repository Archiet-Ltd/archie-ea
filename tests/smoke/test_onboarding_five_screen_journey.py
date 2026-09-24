"""A genuinely new user walks the five onboarding screens end to end.

Unlike tests/smoke/conftest.py's `seeded` fixture (an org with application and
solution rows already in it, so the onboarding redirect never fires for those
archetypes), this fixture is deliberately empty: no applications, no elements,
no capabilities, no vendors -- the exact condition the dashboard.overview
redirect and the retired first-login modal both key off.
"""
import uuid

import pytest

PASSWORD = "OnboardJourney!2026"
PAGE_TIMEOUT = 30000


@pytest.fixture
def fresh_user(live_server):
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User

        db.create_all()
        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()

        org = Organization(name="Fresh Org %s" % suffix, slug="fresh-%s" % suffix)
        db.session.add(org)
        db.session.commit()

        email = "fresh.%s@example.com" % suffix
        user = User(
            email=email, first_name="Fresh", last_name="User",
            organization_id=org.id, confirmed=True,
        )
        user.role = architect_role
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

    return {"email": email}


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url


def test_a_new_user_is_redirected_into_onboarding_not_the_dashboard(live_server, fresh_user, browser):
    page = browser.new_page()
    try:
        _login(page, live_server, fresh_user["email"])
        page.wait_for_timeout(500)
        assert "/onboarding/welcome" in page.url, (
            "a genuinely new user with an empty workspace must land in onboarding, "
            "not the dashboard -- got %s" % page.url
        )
    finally:
        page.close()


def test_the_five_screens_walk_through_to_the_dashboard(live_server, fresh_user, browser):
    page = browser.new_page()
    try:
        _login(page, live_server, fresh_user["email"])
        page.wait_for_timeout(500)

        # Screen 1: Welcome
        assert "/onboarding/welcome" in page.url
        page.click("text=Let's go")
        page.wait_for_url(lambda url: "/onboarding/company" in url, timeout=PAGE_TIMEOUT)

        # Screen 2: Bring your company (the only mandatory step)
        page.check("input[value='early_revenue']")
        page.fill("#company_size", "8 people")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url(lambda url: "/onboarding/first-question" in url, timeout=PAGE_TIMEOUT)

        # Screen 3: First question -- skip it, it must be optional
        page.get_by_role("button", name="Skip", exact=True).click()
        page.wait_for_url(lambda url: "/onboarding/gaps" in url, timeout=PAGE_TIMEOUT)

        # Screen 4: Fill the gaps -- the review section shows the pending proposal count
        assert "1 suggestion waiting for you to confirm or dismiss" in page.inner_text("body")
        # A long list must not be a wall: only the first few show until asked.
        rows = page.get_by_text("Expected at your stage", exact=True)
        assert rows.count() == 6, "the gaps list should open with a short, readable set"
        page.get_by_role("button", name="Show all").click()
        assert rows.count() > 6, "Show all must reveal the rest of the expected items"
        page.get_by_role("link", name="Continue").click()
        page.wait_for_url(lambda url: "/onboarding/twin" in url, timeout=PAGE_TIMEOUT)

        # Screen 5: Your twin -- finish
        page.click("input[value='solution_architect']")
        page.wait_for_timeout(200)
        page.click("button:has-text('Start Architecture Journey'), button:has-text('Go to Dashboard')")
        page.wait_for_url(lambda url: "/onboarding" not in url, timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        assert "/onboarding" not in page.url, "finishing must leave the onboarding flow entirely"
    finally:
        page.close()


def test_the_website_field_is_an_honest_stub_not_a_dead_control(live_server, fresh_user, browser):
    """The reading engine is separate, not-yet-landed work; the button must say
    so rather than silently doing nothing (dead-interactions/fabricated-data)."""
    page = browser.new_page()
    try:
        _login(page, live_server, fresh_user["email"])
        page.wait_for_timeout(500)
        page.click("text=Let's go")
        page.wait_for_url(lambda url: "/onboarding/company" in url, timeout=PAGE_TIMEOUT)
        page.fill("#source_url", "https://example.com")
        page.click("text=Read our site")
        page.wait_for_timeout(500)
        body = page.inner_text("body")
        assert "not available yet" in body.lower() or "saved the address" in body.lower(), (
            "the website field's button must give an honest result, not silently do nothing"
        )
    finally:
        page.close()


def test_a_returning_teammate_enters_at_the_first_question(live_server, fresh_user, browser):
    """onboarding-prd-v1 §3: an invited team member skips Screens 1-2 because
    the org's P0 already exists."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.modules.onboarding.services import profile

        architect_role = Role.query.filter_by(name="Architect").one()
        org = User.query.filter_by(email=fresh_user["email"]).one().organization
        profile.write(org, stage="growing")

        suffix = fresh_user["email"].split("@")[0]
        teammate_email = "teammate.%s@example.com" % suffix
        teammate = User(
            email=teammate_email, first_name="Team", last_name="Mate",
            organization_id=org.id, confirmed=True,
        )
        teammate.role = architect_role
        teammate.password = PASSWORD
        db.session.add(teammate)
        db.session.commit()

    page = browser.new_page()
    try:
        _login(page, live_server, teammate_email)
        page.wait_for_timeout(500)
        assert "/onboarding/first-question" in page.url, (
            "an invited teammate whose org already has a stage recorded should "
            "skip straight to the first question, got %s" % page.url
        )
    finally:
        page.close()
