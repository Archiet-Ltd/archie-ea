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
        page.wait_for_url(lambda url: "/onboarding/capabilities" in url, timeout=PAGE_TIMEOUT)

        # Screen 3: Capabilities -- skip it, it must be optional
        page.get_by_role("link", name="Skip this step").click()
        page.wait_for_url(lambda url: "/onboarding/people" in url, timeout=PAGE_TIMEOUT)

        # People -- also optional; with no capabilities recorded it says so instead of an empty form
        assert "haven't recorded any capabilities" in page.inner_text("body")
        page.get_by_role("link", name="Skip this step").click()
        page.wait_for_url(lambda url: "/onboarding/tools" in url, timeout=PAGE_TIMEOUT)

        # Tools -- also optional
        page.get_by_role("link", name="Skip this step").click()
        page.wait_for_url(lambda url: "/onboarding/goals" in url, timeout=PAGE_TIMEOUT)

        # Goals and changes -- also optional
        page.get_by_role("link", name="Skip this step").click()
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

        # Screen 5: Your twin -- finish (the role was chosen on screen 2)
        page.click(r"button[\@click='finish()']")
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


def test_a_returning_teammate_enters_at_the_capabilities_screen(live_server, fresh_user, browser):
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
        assert "/onboarding/capabilities" in page.url, (
            "an invited teammate whose org already has a stage recorded should "
            "skip straight to the capabilities step, got %s" % page.url
        )
    finally:
        page.close()



def test_a_capability_answer_is_saved_to_the_real_table_and_survives_a_reload(live_server, fresh_user, browser):
    """The wiring proof in the browser: click the real controls on the capabilities
    screen, save, reload, and see the answer come back from the capability table."""
    page = browser.new_page()
    try:
        _login(page, live_server, fresh_user["email"])
        page.wait_for_timeout(500)
        page.click("text=Let's go")
        page.wait_for_url(lambda url: "/onboarding/company" in url, timeout=PAGE_TIMEOUT)
        page.check("input[value='early_revenue']")
        page.fill("#company_name", "Lantern Quay Ltd")
        page.get_by_role("button", name="11 to 50 people").click()
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url(lambda url: "/onboarding/capabilities" in url, timeout=PAGE_TIMEOUT)

        # A small early-revenue company is offered its own set, not procurement.
        body = page.inner_text("body")
        assert "Customer acquisition" in body
        assert "Procurement" not in body

        page.fill("#owner_customer_acquisition", "Sam")
        page.locator("#owner_customer_acquisition").press("Tab")
        page.get_by_role("group", name="Maturity of Customer acquisition").get_by_role("button", name="Level 2, Managed").click()
        page.get_by_role("button", name="Save and continue").click()
        page.wait_for_url(lambda url: "/onboarding/people" in url, timeout=PAGE_TIMEOUT)

        # A company of 11-50 is asked for named people; attach one to the capability just saved.
        assert "name each person" in page.inner_text("body")
        page.fill("#name_0", "Priya Shah")
        page.select_option("#role_0_customer_acquisition", "R")
        page.select_option("#prof_0_customer_acquisition", "3")
        page.get_by_role("button", name="Save and continue").click()
        page.wait_for_url(lambda url: "/onboarding/tools" in url, timeout=PAGE_TIMEOUT)

        # A tool that supports the capability just saved.
        page.fill("#tool_0", "Stripe")
        page.select_option("#deploy_0", "saas")
        page.get_by_label("Customer acquisition").check()
        page.get_by_role("button", name="Save and continue").click()
        page.wait_for_url(lambda url: "/onboarding/goals" in url, timeout=PAGE_TIMEOUT)

        page.goto(live_server + "/onboarding/tools", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        assert page.input_value("#tool_0") == "Stripe"
        assert page.input_value("#deploy_0") == "saas"
        assert page.get_by_label("Customer acquisition").is_checked(), "the support link must come back after a reload"
        page.goto(live_server + "/onboarding/goals", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)

        # A goal, and a change that serves it.
        page.fill("#goal_0", "First ten customers")
        page.fill("#measure_0", "10 signed contracts")
        page.get_by_role("button", name="Add a change").click()
        page.fill("#change_0", "Launch self-service sign-up")
        page.select_option("#status_0", "in_progress")
        page.select_option("#serves_0", "First ten customers")
        page.get_by_role("button", name="Save and continue").click()
        page.wait_for_url(lambda url: "/onboarding/gaps" in url, timeout=PAGE_TIMEOUT)

        page.goto(live_server + "/onboarding/goals", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        assert page.input_value("#goal_0") == "First ten customers"
        assert page.input_value("#measure_0") == "10 signed contracts"
        assert page.input_value("#change_0") == "Launch self-service sign-up"
        assert page.input_value("#status_0") == "in_progress"
        assert page.input_value("#serves_0") == "First ten customers", "the link to the goal must come back after a reload"

        page.goto(live_server + "/onboarding/people", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        assert page.input_value("#name_0") == "Priya Shah"
        assert page.input_value("#role_0_customer_acquisition") == "R"
        assert page.input_value("#prof_0_customer_acquisition") == "3", "the saved proficiency must come back after a reload"

        page.goto(live_server + "/onboarding/company", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        assert page.input_value("#company_name") == "Lantern Quay Ltd", "the company name must replace the auto-created workspace name"

        page.goto(live_server + "/onboarding/capabilities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        assert page.input_value("#owner_customer_acquisition") == "Sam"
        level2 = page.get_by_role("group", name="Maturity of Customer acquisition").get_by_role("button", name="Level 2, Managed")
        assert level2.get_attribute("aria-pressed") == "true", "the saved rating must come back after a reload"
        # Marketing was not answered, so it stays unassessed and was not created.
        marketing = page.get_by_role("group", name="Maturity of Marketing").get_by_role("button", name="Level 2, Managed")
        assert marketing.get_attribute("aria-pressed") == "false"
    finally:
        page.close()
