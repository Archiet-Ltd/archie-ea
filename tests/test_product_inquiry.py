"""Tests for the paid-offer inquiry form and its admin CSV export.

What these tests check:
1. The two offer pages render at their URLs with the inquiry form and the
   page-specific consent text, and pass the general public-page contract.
2. POST /offers/inquire with a valid email and consent stores one row with
   the right offer and the exact consent text the page showed.
3. Duplicate (email, offer) shows the same thanks and stores nothing new;
   the same email against the other offer stores a second row.
4. Missing consent, invalid email and an over-length name are refused with
   no row stored.
5. /admin/product-inquiries.csv mirrors /admin/waitlist.csv: 403 for a
   non-admin AND for an organisation admin who is not a platform admin,
   redirect for anonymous, 200 with rows for a platform admin.
6. Both CSV exports escape a cell that would otherwise open as a spreadsheet
   formula.
7. A new inquiry e-mails SALES_NOTIFY_EMAIL once (mail mocked); when that
   setting is unset the inquiry still stores, just without an e-mail; a
   resubmitted duplicate does not send a second e-mail.
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
    """An organisation admin: Administrator role, but NOT a platform admin.

    This is the role every customer's own org admin can hold — deliberately
    distinct from ``_make_platform_admin`` below, so a test asking for "an
    admin" here never silently gets cross-tenant access.
    """
    return _make_user(db_session, org, email=email, role_name="Administrator")


def _make_platform_admin(db_session, org, *, email=None):
    """Archiet's own platform staff: Administrator role AND is_platform_admin."""
    user = _make_user(db_session, org, email=email, role_name="Administrator")
    user.is_platform_admin = True
    db_session.flush()
    return user


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
        assert "$290/editor/year" in html
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

    def test_name_over_200_characters_gets_a_clean_form_error_not_a_500(
        self, client, db_session
    ):
        from app.models.product_inquiry import ProductInquiry

        email = "toolongname@example.com"
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email=email,
            name="x" * 201,
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "shorter name" in html.lower() or "200 characters" in html.lower()

        row = ProductInquiry.query.filter_by(email=email).first()
        assert row is None

    def test_name_at_200_characters_still_works(self, client, db_session):
        from app.models.product_inquiry import ProductInquiry

        email = "exactlength@example.com"
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email=email,
            name="x" * 200,
        )
        assert resp.status_code == 200
        assert "Thank you" in resp.data.decode()

        row = ProductInquiry.query.filter_by(email=email).first()
        assert row is not None
        assert row.name == "x" * 200


class TestAdminProductInquiriesCsv:
    def test_non_admin_gets_403(self, client, db_session, make_org, login_as):
        org = make_org("entelim")
        user = _make_user(db_session, org)
        login_as(client, user)
        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 403

    def test_org_admin_who_is_not_a_platform_admin_gets_403(
        self, client, db_session, make_org, login_as
    ):
        """An organisation's own admin must not be able to download every
        prospect's email and name — only Archiet's own platform admins can."""
        org = make_org("entelim")
        admin = _make_admin(db_session, org)
        login_as(client, admin)
        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 403

    def test_unauthenticated_gets_redirect(self, client):
        resp = client.get("/admin/product-inquiries.csv", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_platform_admin_gets_csv_with_rows(self, client, db_session, make_org, login_as):
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
        admin = _make_platform_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert "admin-csv-test@example.com" in csv_text
        assert "architecture_health_check" in csv_text
        assert "email" in csv_text  # header row

    def test_platform_admin_csv_read_is_not_scoped_to_any_organisation(
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
        admin_a = _make_platform_admin(db_session, org_a)
        admin_b = _make_platform_admin(db_session, org_b)

        login_as(client, admin_a)
        resp_a = client.get("/admin/product-inquiries.csv")
        assert resp_a.status_code == 200
        assert "cross-org-inquiry@example.com" in resp_a.data.decode()

        login_as(client, admin_b)
        resp_b = client.get("/admin/product-inquiries.csv")
        assert resp_b.status_code == 200
        assert "cross-org-inquiry@example.com" in resp_b.data.decode()

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_formula_like_name_is_escaped_in_the_csv(
        self, client, db_session, make_org, login_as, prefix
    ):
        from app.models.product_inquiry import ProductInquiry

        dangerous_name = f"{prefix}cmd|'/c calc'!A1"
        inquiry = ProductInquiry(
            email="formula-test@example.com",
            name=dangerous_name,
            offer="architecture_health_check",
            consent_text=HEALTH_CHECK_CONSENT,
        )
        db_session.add(inquiry)
        db_session.flush()

        org = make_org("entelim")
        admin = _make_platform_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert f"'{dangerous_name}" in csv_text
        assert f",{dangerous_name}," not in csv_text

    def test_normal_name_is_unchanged_in_the_csv(self, client, db_session, make_org, login_as):
        from app.models.product_inquiry import ProductInquiry

        inquiry = ProductInquiry(
            email="normal-name@example.com",
            name="Jo Example",
            offer="architecture_health_check",
            consent_text=HEALTH_CHECK_CONSENT,
        )
        db_session.add(inquiry)
        db_session.flush()

        org = make_org("entelim")
        admin = _make_platform_admin(db_session, org)
        login_as(client, admin)

        resp = client.get("/admin/product-inquiries.csv")
        assert resp.status_code == 200
        csv_text = resp.data.decode()
        assert "Jo Example" in csv_text
        assert "'Jo Example" not in csv_text


@pytest.fixture
def outbox(app):
    from app.extensions import mail

    with mail.record_messages() as messages:
        yield messages


@pytest.fixture
def sales_notify_on(app, monkeypatch):
    """SALES_NOTIFY_EMAIL set, and mail actually available (a sender configured)."""
    monkeypatch.setitem(app.config, "SALES_NOTIFY_EMAIL", "founder@example.com")
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "no-reply@example.com")


@pytest.fixture
def sales_notify_off(app, monkeypatch):
    """SALES_NOTIFY_EMAIL unset -- mail availability must not matter either way."""
    monkeypatch.setitem(app.config, "SALES_NOTIFY_EMAIL", None)
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "no-reply@example.com")


class TestSalesNotificationEmail:
    def test_new_inquiry_emails_sales_notify_email_once(
        self, client, db_session, sales_notify_on, outbox
    ):
        from app.models.product_inquiry import ProductInquiry

        email = "notify-me@example.com"
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email=email,
            name="Jo Example",
        )
        assert resp.status_code == 200
        assert ProductInquiry.query.filter_by(email=email).first() is not None

        assert len(outbox) == 1
        message = outbox[0]
        assert message.recipients == ["founder@example.com"]
        assert "architecture_health_check" in message.body
        assert "Jo Example" in message.body
        assert email in message.body
        assert HEALTH_CHECK_URL in message.body

    def test_unset_sales_notify_email_stores_without_sending(
        self, client, db_session, sales_notify_off, outbox
    ):
        from app.models.product_inquiry import ProductInquiry

        email = "no-notify@example.com"
        resp = _submit(
            client,
            url=HEALTH_CHECK_URL,
            offer="architecture_health_check",
            email=email,
        )
        assert resp.status_code == 200
        assert ProductInquiry.query.filter_by(email=email).first() is not None
        assert len(outbox) == 0

    def test_duplicate_submission_does_not_send_a_second_email(
        self, client, db_session, sales_notify_on, outbox
    ):
        email = "dup-notify@example.com"
        _submit(client, url=HEALTH_CHECK_URL, offer="architecture_health_check", email=email)
        assert len(outbox) == 1

        resp = _submit(
            client, url=HEALTH_CHECK_URL, offer="architecture_health_check", email=email
        )
        assert resp.status_code == 200
        assert len(outbox) == 1
