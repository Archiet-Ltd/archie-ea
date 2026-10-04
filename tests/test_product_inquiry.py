"""Tests for the paid-offer inquiry form and its admin CSV export.

What these tests check:
1. The two offer pages render at their URLs with the inquiry form and the
   page-specific consent text, and pass the general public-page contract.
2. POST /offers/inquire with a valid email and consent stores one row with
   the right offer and the exact consent text the page showed.
3. Duplicate (email, offer) shows the same thanks and stores nothing new;
   the same email against the other offer stores a second row.
4. Missing consent and invalid email are refused with no row stored.
5. /admin/product-inquiries.csv mirrors /admin/waitlist.csv: 403 for a
   non-admin, redirect for anonymous, 200 with rows for an admin.
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
        email=email or f"inquiry-{uuid.uuid4().hex[:8]}@example.com",
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


HEALTH_CHECK_URL = "/architecture-health-check"
TEAM_ANNUAL_URL = "/team-annual-onboarding"

HEALTH_CHECK_CONSENT = (
    "Used to follow up about pricing and scheduling for the architecture "
    "health check you requested."
)
TEAM_ANNUAL_CONSENT = (
    "Used to follow up about pricing and scheduling for the Team annual "
    "plan with onboarding you requested."
)


def _submit(client, *, url, offer, email, name="", consent="1"):
    data = {
        "offer": offer,
        "family": "site",
        "slug": url.lstrip("/"),
        "email": email,
        "name": name,
    }
    if consent:
        data["consent"] = consent
    return client.post("/offers/inquire", data=data, follow_redirects=True)


class TestOfferPagesRender:
    def test_architecture_health_check_page_renders_price_and_form(self, client):
        resp = client.get(HEALTH_CHECK_URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Architecture health check" in html
        assert "$4,500" in html
        assert 'data-testid="offer-inquiry-form"' in html
        assert HEALTH_CHECK_CONSENT in html

    def test_team_annual_onboarding_page_renders_both_prices_and_form(self, client):
        resp = client.get(TEAM_ANNUAL_URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Team annual plan with onboarding" in html
        assert "$2,900" in html
        assert "$1,500" in html
        assert 'data-testid="offer-inquiry-form"' in html
        assert TEAM_ANNUAL_CONSENT in html

    def test_offer_pages_do_not_render_stripe_checkout_cta(self, client):
        """These pages must not pick up the cta=plans checkout branch."""
        for url in (HEALTH_CHECK_URL, TEAM_ANNUAL_URL):
            html = client.get(url).data.decode()
            assert 'data-testid="pricing-buy"' not in html
            assert "Join the waiting list" not in html


class TestProductInquirySubmit:
    def test_valid_submission_stores_row_with_offer_and_consent_text(self, client, db_session):
        from app.models.product_inquiry import ProductInquiry

        email = "buyer@example.com"
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email=email,
            name="Jo Example",
        )
        assert resp.status_code == 200
        assert "Thank you" in resp.data.decode()

        row = ProductInquiry.query.filter_by(email=email).first()
        assert row is not None
        assert row.offer == "architecture_health_check"
        assert row.name == "Jo Example"
        assert row.consent_text == HEALTH_CHECK_CONSENT

    def test_duplicate_email_same_offer_shows_same_thanks_and_stores_nothing_new(
        self, client, db_session
    ):
        from app.models.product_inquiry import ProductInquiry

        email = "dup-offer@example.com"
        _submit(client, url=HEALTH_CHECK_URL, offer="architecture_health_check", email=email)
        count_before = ProductInquiry.query.filter_by(email=email).count()

        resp = _submit(
            client, url=HEALTH_CHECK_URL, offer="architecture_health_check", email=email
        )
        assert resp.status_code == 200
        assert "Thank you" in resp.data.decode()

        count_after = ProductInquiry.query.filter_by(email=email).count()
        assert count_after == count_before == 1

    def test_same_email_different_offer_stores_a_second_row(self, client, db_session):
        from app.models.product_inquiry import ProductInquiry

        email = "both-offers@example.com"
        _submit(client, url=HEALTH_CHECK_URL, offer="architecture_health_check", email=email)
        _submit(client, url=TEAM_ANNUAL_URL, offer="team_annual_onboarding", email=email)

        rows = ProductInquiry.query.filter_by(email=email).order_by(ProductInquiry.offer).all()
        assert [r.offer for r in rows] == ["architecture_health_check", "team_annual_onboarding"]
        assert rows[1].consent_text == TEAM_ANNUAL_CONSENT

    def test_missing_consent_refuses_with_clear_message(self, client, db_session):
        from app.models.product_inquiry import ProductInquiry

        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email="noconsent@example.com",
            consent=None,
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "must agree" in html.lower() or "agree to be contacted" in html.lower()

        row = ProductInquiry.query.filter_by(email="noconsent@example.com").first()
        assert row is None

    def test_missing_email_refuses_with_clear_message(self, client, db_session):
        from app.models.product_inquiry import ProductInquiry

        count_before = ProductInquiry.query.count()
        resp = _submit(
            client, url=HEALTH_CHECK_URL, offer="architecture_health_check", email=""
        )
        assert resp.status_code == 200
        assert "email" in resp.data.decode().lower()
        assert ProductInquiry.query.count() == count_before

    def test_invalid_email_format_refuses_with_clear_message(self, client, db_session):
        from app.models.product_inquiry import ProductInquiry

        count_before = ProductInquiry.query.count()
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email="not-an-email",
        )
        assert resp.status_code == 200
        assert "valid email" in resp.data.decode().lower()
        assert ProductInquiry.query.count() == count_before

    def test_offer_field_not_matching_the_page_is_refused(self, client, db_session):
        """The hidden offer field must match the page it claims to come from."""
        from app.models.product_inquiry import ProductInquiry

        count_before = ProductInquiry.query.count()
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="team_annual_onboarding",  # mismatched on purpose
            email="mismatch@example.com",
        )
        assert resp.status_code == 200
        assert ProductInquiry.query.count() == count_before

    def test_unknown_page_returns_404(self, client):
        resp = client.post(
            "/offers/inquire",
            data={
                "offer": "architecture_health_check",
                "family": "site",
                "slug": "no-such-page",
                "email": "x@example.com",
                "consent": "1",
            },
        )
        assert resp.status_code == 404


class TestAdminProductInquiriesCsv:
    def test_non_admin_gets_403(self, client, db_session, make_org, login_as):
        org = make_org("entelim")
        user = _make_user(db_session, org)
        login_as(client, user)
        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 403

    def test_unauthenticated_gets_redirect(self, client):
        resp = client.get("/admin/product-inquiries.csv", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_admin_gets_csv_with_rows(self, client, db_session, make_org, login_as):
        from app.models.product_inquiry import ProductInquiry

        inquiry = ProductInquiry(
            email="admin-csv-test@example.com",
            name="Admin Tester",
            offer="architecture_health_check",
            consent_text=HEALTH_CHECK_CONSENT,
        )
        db_session.add(inquiry)
        db_session.flush()

        org = make_org("entelim")
        admin = _make_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert "admin-csv-test@example.com" in csv_text
        assert "architecture_health_check" in csv_text
        assert "email" in csv_text  # header row

    def test_admin_csv_read_is_not_scoped_to_any_organisation(
        self, client, db_session, make_org, login_as
    ):
        from app.models.product_inquiry import ProductInquiry

        inquiry = ProductInquiry(
            email="cross-org-inquiry@example.com",
            offer="team_annual_onboarding",
            consent_text=TEAM_ANNUAL_CONSENT,
        )
        db_session.add(inquiry)
        db_session.flush()

        org_a = make_org("entelim-a")
        org_b = make_org("entelim-b")
        admin_a = _make_admin(db_session, org_a)
        admin_b = _make_admin(db_session, org_b)

        login_as(client, admin_a)
        resp_a = client.get("/admin/product-inquiries.csv")
        assert resp_a.status_code == 200
        assert "cross-org-inquiry@example.com" in resp_a.data.decode()

        login_as(client, admin_b)
        resp_b = client.get("/admin/product-inquiries.csv")
        assert resp_b.status_code == 200
        assert "cross-org-inquiry@example.com" in resp_b.data.decode()
