"""Tests for the Entelim public home page and waiting list.

What these tests check:
1. GET / signed out returns 200 with "Entelim" in title/h1, none of the old
   names; the home page offers "Start free" to registration, not a waitlist
   form.
2. POST with valid email+consent stores one row; duplicate stores nothing
   new; without consent refuses; without CSRF refused. (The home page no
   longer renders a waitlist form or confirmation copy -- see
   TestWaitlistSignup's own docstring.)
3. /admin/waitlist.csv returns 403 for non-admin AND for an organisation
   admin who is not a platform admin; 200 with rows for a platform admin.
4. Cross-organisation: a platform admin from any org sees the same global
   rows; a plain organisation admin cannot reach the export at all.
"""

import uuid

import pytest


def _make_user(db_session, org, *, email=None, role_name="Architect"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()

    user = User(
        email=email or f"entelim-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _make_admin(db_session, org, *, email=None):
    """An organisation admin: Administrator role, but NOT a platform admin."""
    return _make_user(db_session, org, email=email, role_name="Administrator")


def _make_platform_admin(db_session, org, *, email=None):
    """Archiet's own platform staff: Administrator role AND is_platform_admin."""
    user = _make_user(db_session, org, email=email, role_name="Administrator")
    user.is_platform_admin = True
    db_session.flush()
    return user


class TestHomePage:
    def test_home_page_has_entelim_title_and_heading(self, client):
        """AC 1: GET / signed out returns 200 with Entelim, none of old names."""
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Entelim" in html
        assert "A.R.C.H.I.E" not in html
        assert "Dashboard" not in html
        assert "Enterprise Architecture Platform" not in html

    def test_home_page_has_start_free_cta_not_a_waitlist_form(self, client):
        """Community sign-up is open to the public: the home page's primary
        action is "Start free" to registration, and the waitlist form (email
        and consent fields) is gone."""
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Start free" in html
        assert 'id="email"' not in html
        assert 'id="consent"' not in html
        assert "Join the waiting list" not in html

    def test_signed_in_user_is_redirected_to_dashboard(self, client, db_session, make_org, login_as):
        """Signed-in visitors redirect to the dashboard."""
        org = make_org("entelim")
        user = _make_user(db_session, org)
        login_as(client, user)
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["Location"]


class TestWaitlistSignup:
    """The home page no longer has a waitlist form or submit button (Start
    free to registration is the primary action now), so POSTing to "/" is
    no longer reachable from the rendered page and there is no more visible
    "Thank you" or error text -- that template block was removed with the
    form. The POST route and the WaitlistSignup table it writes to are
    untouched and still used by the admin CSV export below, so these tests
    check the stored row, not rendered copy that no longer exists.
    """

    def test_valid_signup_stores_one_row(self, client, db_session):
        """AC 2: POST with valid email and consent stores one row."""
        from app.models.waitlist_signup import WaitlistSignup

        resp = client.post(
            "/",
            data={"email": "test@example.com", "consent": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        row = WaitlistSignup.query.filter_by(email="test@example.com").first()
        assert row is not None
        assert row.source == "home_page"
        assert "launch news" in row.consent_text

    def test_duplicate_email_shows_same_thanks_and_stores_nothing_new(self, client, db_session):
        """AC 2: Duplicate email stores no second row; count remains 1."""
        from app.models.waitlist_signup import WaitlistSignup

        # First signup
        client.post("/", data={"email": "dup@example.com", "consent": "1"}, follow_redirects=True)
        count_before = WaitlistSignup.query.filter_by(email="dup@example.com").count()

        # Duplicate
        resp = client.post("/", data={"email": "dup@example.com", "consent": "1"}, follow_redirects=True)
        assert resp.status_code == 200

        count_after = WaitlistSignup.query.filter_by(email="dup@example.com").count()
        assert count_after == count_before
        assert count_after == 1

    def test_missing_consent_refuses_with_clear_message(self, client, db_session):
        """AC 2: Without consent, no row is stored."""
        from app.models.waitlist_signup import WaitlistSignup

        resp = client.post("/", data={"email": "noconsent@example.com"}, follow_redirects=True)
        assert resp.status_code == 200

        row = WaitlistSignup.query.filter_by(email="noconsent@example.com").first()
        assert row is None

    def test_missing_email_refuses_with_clear_message(self, client, db_session):
        """AC 2: Without email it refuses with a clear message."""
        from app.models.waitlist_signup import WaitlistSignup

        count_before = WaitlistSignup.query.count()
        resp = client.post("/", data={"consent": "1"}, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "email" in html.lower()

        # No new rows should have been added
        assert WaitlistSignup.query.count() == count_before

    def test_invalid_email_format_refuses_with_clear_message(self, client, db_session):
        """Server-side email validation rejects invalid formats."""
        from app.models.waitlist_signup import WaitlistSignup

        count_before = WaitlistSignup.query.count()
        resp = client.post(
            "/",
            data={"email": "not-an-email", "consent": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert WaitlistSignup.query.count() == count_before

    def test_missing_csrf_is_refused(self, client, app, db_session):
        """AC 2: Without CSRF it is refused."""
        from app.models.waitlist_signup import WaitlistSignup

        app.config["WTF_CSRF_ENABLED"] = True
        try:
            client.post(
                "/",
                data={"email": "csrf@example.com", "consent": "1"},
                follow_redirects=True,
            )
            # With CSRF enabled and no token, the request should not process
            # the form, so no row should be stored.
            row = WaitlistSignup.query.filter_by(email="csrf@example.com").first()
            assert row is None
        finally:
            app.config["WTF_CSRF_ENABLED"] = False


class TestAdminWaitlistCsv:
    def test_non_admin_gets_403_on_waitlist_csv(self, client, db_session, make_org, login_as):
        """AC 3: /admin/waitlist.csv returns 403 for a non-admin."""
        org = make_org("entelim")
        user = _make_user(db_session, org)
        login_as(client, user)
        resp = client.get("/admin/waitlist.csv")
        assert resp.status_code == 403

    def test_org_admin_who_is_not_a_platform_admin_gets_403_on_waitlist_csv(
        self, client, db_session, make_org, login_as
    ):
        """An organisation's own admin must not be able to download every
        prospect's email and name — only Archiet's own platform admins can."""
        org = make_org("entelim")
        admin = _make_admin(db_session, org)
        login_as(client, admin)
        resp = client.get("/admin/waitlist.csv")
        assert resp.status_code == 403

    def test_platform_admin_gets_csv_with_rows(self, client, db_session, make_org, login_as):
        """AC 3: Platform admin gets 200 with CSV containing rows."""
        from app.models.waitlist_signup import WaitlistSignup

        # Seed a row
        signup = WaitlistSignup(
            email="admin-test@example.com",
            source="home_page",
            consent_text="Email used only for launch news about Entelim.",
        )
        db_session.add(signup)
        db_session.flush()

        org = make_org("entelim")
        admin = _make_platform_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/waitlist.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert "admin-test@example.com" in csv_text
        assert "email" in csv_text  # header row

    def test_unauthenticated_gets_redirect_on_waitlist_csv(self, client):
        """Unauthenticated request is redirected."""
        resp = client.get("/admin/waitlist.csv", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_platform_admin_csv_read_is_not_scoped_to_any_organisation(
        self, client, db_session, make_org, login_as
    ):
        """Cross-organisation: a platform admin from org A and org B both see the same global rows."""
        from app.models.waitlist_signup import WaitlistSignup

        # Seed a row
        signup = WaitlistSignup(
            email="cross-org@example.com",
            source="home_page",
            consent_text="Email used only for launch news about Entelim.",
        )
        db_session.add(signup)
        db_session.flush()

        org_a = make_org("entelim-a")
        org_b = make_org("entelim-b")
        admin_a = _make_platform_admin(db_session, org_a)
        admin_b = _make_platform_admin(db_session, org_b)

        login_as(client, admin_a)
        resp_a = client.get("/admin/waitlist.csv")
        assert resp_a.status_code == 200
        assert "cross-org@example.com" in resp_a.data.decode()

        login_as(client, admin_b)
        resp_b = client.get("/admin/waitlist.csv")
        assert resp_b.status_code == 200
        assert "cross-org@example.com" in resp_b.data.decode()

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_formula_like_email_source_is_escaped_in_the_csv(
        self, client, db_session, make_org, login_as, prefix
    ):
        from app.models.waitlist_signup import WaitlistSignup

        dangerous_source = f"{prefix}cmd|'/c calc'!A1"
        signup = WaitlistSignup(
            email="formula-test@example.com",
            source=dangerous_source,
            consent_text="Email used only for launch news about Entelim.",
        )
        db_session.add(signup)
        db_session.flush()

        org = make_org("entelim")
        admin = _make_platform_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/waitlist.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert f"'{dangerous_source}" in csv_text

    def test_normal_source_is_unchanged_in_the_csv(self, client, db_session, make_org, login_as):
        from app.models.waitlist_signup import WaitlistSignup

        signup = WaitlistSignup(
            email="plain-source@example.com",
            source="home_page",
            consent_text="Email used only for launch news about Entelim.",
        )
        db_session.add(signup)
        db_session.flush()

        org = make_org("entelim")
        admin = _make_platform_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/waitlist.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert "home_page" in csv_text
        assert "'home_page" not in csv_text