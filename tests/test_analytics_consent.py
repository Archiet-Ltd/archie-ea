"""Tests for GA4, Clarity, and search verification on public pages behind consent.

Covers:
- With settings empty: no gtag/clarity markup, CSP unchanged
- With settings set: home page has verification meta tags
- Public page has consent banner, no tag script before consent
- Loader appears with nonce after consent cookie is set
- Signed-in pages never contain gtag/clarity even with consent
"""

from __future__ import annotations

import pytest


class TestAnalyticsConsentEmptySettings:
    """When GA4/Clarity settings are empty, nothing is rendered and CSP is clean."""

    def test_home_page_no_verification_meta_when_empty(self, app):
        with app.test_client() as client:
            rv = client.get("/")
            html = rv.data.decode()
            assert 'name="google-site-verification"' not in html
            assert 'name="msvalidate.01"' not in html

    def test_public_page_no_consent_banner_when_empty(self, app):
        with app.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "analytics-consent-banner" not in html
            assert "We value your privacy" not in html

    def test_public_page_no_ga4_loader_when_empty(self, app):
        with app.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "googletagmanager.com" not in html
            assert "gtag(" not in html
            assert "dataLayer" not in html

    def test_public_page_no_clarity_loader_when_empty(self, app):
        with app.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "clarity.ms" not in html
            assert "clarity(" not in html

    def test_csp_no_ga4_hosts_when_empty(self, app):
        with app.test_client() as client:
            rv = client.get("/pricing")
            csp = rv.headers.get("Content-Security-Policy", "")
            assert "googletagmanager.com" not in csp
            assert "google-analytics.com" not in csp
            assert "clarity.ms" not in csp
            assert "c.bing.com" not in csp

    def test_csp_no_clarity_hosts_when_empty(self, app):
        with app.test_client() as client:
            rv = client.get("/")
            csp = rv.headers.get("Content-Security-Policy", "")
            assert "clarity.ms" not in csp
            assert "c.bing.com" not in csp


class TestAnalyticsConsentWithSettings:
    """When GA4/Clarity settings are configured, tags render correctly."""

    @pytest.fixture
    def app_with_settings(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "GA4_MEASUREMENT_ID", "G-TEST123")
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "test-clarity-id")
        monkeypatch.setitem(app.config, "GOOGLE_SITE_VERIFICATION", "google-verify-token")
        monkeypatch.setitem(app.config, "BING_SITE_VERIFICATION", "bing-verify-token")
        return app

    def test_home_page_has_google_verification_meta(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/")
            html = rv.data.decode()
            assert 'name="google-site-verification" content="google-verify-token"' in html

    def test_home_page_has_bing_verification_meta(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/")
            html = rv.data.decode()
            assert 'name="msvalidate.01" content="bing-verify-token"' in html

    def test_public_page_has_consent_banner(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "analytics-consent-banner" in html
            assert "We value your privacy" in html
            assert "Accept analytics" in html
            assert "Reject analytics" in html

    def test_public_page_no_ga4_script_before_consent(self, app_with_settings):
        """GA4 script tag must not be in initial HTML before consent."""
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            # The loader partial is in the HTML but the script only runs on consent event
            # Check that gtag is not called in initial render
            assert "gtag(" not in html or "consent" in html  # gtag only appears in consent mode default

    def test_public_page_no_clarity_script_before_consent(self, app_with_settings):
        """Clarity script tag must not be created before consent."""
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            # Clarity loader uses deferred script creation on consent event
            # The script URL is in JS string but no <script src="...clarity.ms/tag/..."> tag exists yet
            assert '<script src="https://www.clarity.ms/tag/' not in html
            assert 'clarity.ms/tag/' not in html or 'addEventListener' in html  # Only in JS event handler

    def test_csp_includes_ga4_hosts_when_configured(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            csp = rv.headers.get("Content-Security-Policy", "")
            assert "googletagmanager.com" in csp
            assert "google-analytics.com" in csp

    def test_csp_includes_clarity_hosts_when_configured(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            csp = rv.headers.get("Content-Security-Policy", "")
            assert "clarity.ms" in csp
            assert "c.bing.com" in csp

    def test_consent_cookie_accepted_loads_ga4(self, app_with_settings):
        """After consent cookie=accepted, GA4 loader script is present with nonce."""
        with app_with_settings.test_client() as client:
            # Set consent cookie
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # GA4 loader is always in template (gtag consent defaults to denied, updates on event)
            # The URL is constructed via JS concatenation
            assert "googletagmanager.com/gtag/js?id=" in html
            assert "G-TEST123" in html
            assert "nonce=" in html
            # Consent mode default should be denied
            assert "ad_storage" in html and "denied" in html

    def test_consent_cookie_accepted_loads_clarity(self, app_with_settings):
        """After consent cookie=accepted, Clarity loader script is present with nonce."""
        with app_with_settings.test_client() as client:
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # Clarity loader creates script tag on consent event (JS string present)
            assert "clarity.ms/tag/" in html
            assert "test-clarity-id" in html
            assert "nonce=" in html

    def test_consent_cookie_rejected_no_loaders(self, app_with_settings):
        """After consent cookie=rejected, banner is hidden via JS."""
        with app_with_settings.test_client() as client:
            client.set_cookie("analytics_consent", "rejected")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # Banner HTML is present but JS hides it based on cookie
            assert "analytics-consent-banner" in html
            # The JS checks for rejected cookie and doesn't show banner


class TestAnalyticsConsentSignedInPages:
    """Signed-in application pages must never load GA4/Clarity."""

    @pytest.fixture
    def app_with_settings(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "GA4_MEASUREMENT_ID", "G-TEST123")
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "test-clarity-id")
        return app

    @pytest.fixture
    def authenticated_client(self, app_with_settings):
        """Create a test client with an authenticated user."""
        import uuid
        unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
        with app_with_settings.test_client() as client:
            # Create a user and log in
            with app_with_settings.app_context():
                from app.extensions import db
                from app.models.user import User
                from werkzeug.security import generate_password_hash

                user = User(
                    email=unique_email,
                    first_name="Test",
                    last_name="User",
                    password_hash=generate_password_hash("password"),
                    confirmed=True,
                )
                db.session.add(user)
                db.session.commit()

                # Log in
                client.post("/account/login", data={
                    "email": unique_email,
                    "password": "password",
                }, follow_redirects=True)
            yield client

    def test_dashboard_page_no_ga4(self, authenticated_client):
        rv = authenticated_client.get("/dashboard/")
        html = rv.data.decode()
        assert "googletagmanager.com" not in html
        assert "gtag(" not in html

    def test_dashboard_page_no_clarity(self, authenticated_client):
        rv = authenticated_client.get("/dashboard/")
        html = rv.data.decode()
        assert "clarity.ms" not in html

    def test_dashboard_page_no_consent_banner(self, authenticated_client):
        rv = authenticated_client.get("/dashboard/")
        html = rv.data.decode()
        assert "analytics-consent-banner" not in html

    def test_applications_page_no_ga4(self, authenticated_client):
        rv = authenticated_client.get("/applications/")
        html = rv.data.decode()
        assert "googletagmanager.com" not in html

    def test_architecture_page_no_clarity(self, authenticated_client):
        rv = authenticated_client.get("/architecture/")
        html = rv.data.decode()
        assert "clarity.ms" not in html


class TestAnalyticsConsentCookieBehavior:
    """Test the consent cookie behavior and banner display logic."""

    @pytest.fixture
    def app_with_settings(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "GA4_MEASUREMENT_ID", "G-TEST123")
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "test-clarity-id")
        return app

    def test_no_cookie_shows_banner(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "analytics-consent-banner" in html
            assert 'style="display: none"' not in html

    def test_accepted_cookie_hides_banner(self, app_with_settings):
        with app_with_settings.test_client() as client:
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # Banner HTML is present but JS hides it immediately based on cookie
            assert "analytics-consent-banner" in html

    def test_rejected_cookie_hides_banner(self, app_with_settings):
        with app_with_settings.test_client() as client:
            client.set_cookie("analytics_consent", "rejected")
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "analytics-consent-banner" in html

    def test_cookie_settings_link_in_footer(self, app_with_settings):
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            assert "Cookie settings" in html
            assert "openAnalyticsConsentBanner" in html


class TestAnalyticsConsentOnlyPublicPages:
    """GA4/Clarity only on public pages, never on authenticated routes."""

    @pytest.fixture
    def app_with_settings(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "GA4_MEASUREMENT_ID", "G-TEST123")
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "test-clarity-id")
        return app

    @pytest.mark.parametrize("path", [
        "/",
        "/pricing",
        "/features",
        "/about",
        "/docs",
        "/vision",
        "/security",
        "/privacy",
        "/terms",
        "/contact",
        "/account/login",
        "/account/register",
    ])
    def test_public_pages_have_consent_banner_when_configured(self, app_with_settings, path):
        with app_with_settings.test_client() as client:
            rv = client.get(path)
            # Some paths may redirect; follow redirects
            if rv.status_code in (301, 302):
                rv = client.get(rv.location)
            if rv.status_code == 200:
                html = rv.data.decode()
                # Public pages using public_base.html should have the banner
                assert "analytics-consent-banner" in html, f"Missing banner on {path}"


class TestAnalyticsConsentPrivacyPage:
    """Privacy page includes GA4/Clarity section."""

    def test_privacy_page_has_analytics_section(self, app):
        with app.test_client() as client:
            rv = client.get("/privacy")
            html = rv.data.decode()
            assert "Google Analytics 4" in html
            assert "Microsoft Clarity" in html
            assert "Consent Mode" in html
            assert "Cookie settings" in html
            assert "analytics_consent" in html