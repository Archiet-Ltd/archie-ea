"""Tests for the Entelim public home page and waiting list.

What these tests check:
1. GET / signed out returns 200 with "Entelim" in title/h1, none of the old names.
2. POST with valid email+consent stores one row; duplicate shows same thanks;
   without consent refuses; without CSRF refused.
3. /admin/waitlist.csv returns 403 for non-admin, rows for admin.
4. Cross-organisation: admin from any org sees the same global rows.
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
    return _make_user(db_session, org, email=email, role_name="Administrator")


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

    def test_home_page_has_waitlist_form(self, client):
        """The waitlist form with email and consent fields is present."""
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="email"' in html
        assert 'id="consent"' in html
        assert "Join the waiting list" in html

    def test_signed_in_user_is_redirected_to_dashboard(self, client, db_session, make_org, login_as):
        """Signed-in visitors redirect to the dashboard."""
        org = make_org("entelim")
        user = _make_user(db_session, org)
        login_as(client, user)
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["Location"]


class TestWaitlistSignup:
    def test_valid_signup_stores_one_row(self, client, db_session):
        """AC 2: POST with valid email and consent stores one row."""
        from app.models.waitlist_signup import WaitlistSignup

        resp = client.post(
            "/",
            data={"email": "test@example.com", "consent": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Thank you" in html

        row = WaitlistSignup.query.filter_by(email="test@example.com").first()
        assert row is not None
        assert row.source == "home_page"
        assert "launch news" in row.consent_text

    def test_duplicate_email_shows_same_thanks_and_stores_nothing_new(self, client, db_session):
        """AC 2: Duplicate email shows same thanks, count remains 1."""
        from app.models.waitlist_signup import WaitlistSignup

        # First signup
        client.post("/", data={"email": "dup@example.com", "consent": "1"}, follow_redirects=True)
        count_before = WaitlistSignup.query.filter_by(email="dup@example.com").count()

        # Duplicate
        resp = client.post("/", data={"email": "dup@example.com", "consent": "1"}, follow_redirects=True)
        assert resp.status_code == 200
        assert "Thank you" in resp.data.decode()

        count_after = WaitlistSignup.query.filter_by(email="dup@example.com").count()
        assert count_after == count_before
        assert count_after == 1

    def test_missing_consent_refuses_with_clear_message(self, client, db_session):
        """AC 2: Without consent it refuses with a clear message."""
        from app.models.waitlist_signup import WaitlistSignup

        resp = client.post("/", data={"email": "noconsent@example.com"}, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "must agree" in html.lower() or "agree that" in html.lower()

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
        html = resp.data.decode()
        assert "valid email" in html.lower()
        assert WaitlistSignup.query.count() == count_before

    def test_missing_csrf_is_refused(self, client, app):
        """AC 2: Without CSRF it is refused."""
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            resp = client.post(
                "/",
                data={"email": "csrf@example.com", "consent": "1"},
                follow_redirects=True,
            )
            # With CSRF enabled and no token, the request should not process the form.
            html = resp.data.decode()
            assert "Thank you" not in html
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

    def test_admin_gets_csv_with_rows(self, client, db_session, make_org, login_as):
        """AC 3: Admin gets 200 with CSV containing rows."""
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
        admin = _make_admin(db_session, org)
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

    def test_admin_csv_read_is_not_scoped_to_any_organisation(self, client, db_session, make_org, login_as):
        """Cross-organisation: admin from org A and org B both see the same global rows."""
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
        admin_a = _make_admin(db_session, org_a)
        admin_b = _make_admin(db_session, org_b)

        login_as(client, admin_a)
        resp_a = client.get("/admin/waitlist.csv")
        assert resp_a.status_code == 200
        assert "cross-org@example.com" in resp_a.data.decode()

        login_as(client, admin_b)
        resp_b = client.get("/admin/waitlist.csv")
        assert resp_b.status_code == 200
        assert "cross-org@example.com" in resp_b.data.decode()