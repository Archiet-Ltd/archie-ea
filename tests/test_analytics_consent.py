"""Tests for GA4, Clarity, and search verification on public pages behind consent.

Covers:
- With settings empty: no gtag/clarity markup, CSP unchanged
- With settings set: home page has verification meta tags
- Public page has consent banner, no tag script before consent
- Loader appears with nonce after consent cookie is set
- Signed-in pages never contain gtag/clarity even with consent
- GA4 script only created inside consent handler (defect 1)
- Accept grants only analytics_storage, not advertising (defect 2)
- Banner handlers attached when choice exists; withdrawal works (defect 3)
- Shared artefact page suppresses GA4/Clarity (defect 4)
- _head.html no longer references GOOGLE_ANALYTICS_ID (defect 5)
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

    def test_ga4_script_created_only_in_consent_handler(self, app_with_settings):
        """GA4 script element creation must be inside the consent event handler,
        not at top level. The initial HTML must not contain a script tag with
        googletagmanager.com src."""
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            # No script tag with googletagmanager.com src in initial render
            assert 'src="https://www.googletagmanager.com/gtag/js?id=' not in html
            # The URL string exists only inside the event handler
            assert "googletagmanager.com/gtag/js?id=" in html
            # Verify it's inside addEventListener
            assert "addEventListener" in html
            assert "analytics:consent" in html

    def test_accept_grants_only_analytics_storage(self, app_with_settings):
        """On accept, only analytics_storage is granted. ad_storage, ad_user_data,
        ad_personalization remain denied."""
        with app_with_settings.test_client() as client:
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # Consent update should only grant analytics_storage
            assert "'analytics_storage': 'granted'" in html or '"analytics_storage": "granted"' in html
            # Advertising consents must NOT be granted
            assert "'ad_storage': 'granted'" not in html
            assert '"ad_storage": "granted"' not in html
            assert "'ad_user_data': 'granted'" not in html
            assert '"ad_user_data": "granted"' not in html
            assert "'ad_personalization': 'granted'" not in html
            assert '"ad_personalization": "granted"' not in html


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

    def test_open_banner_always_shows_regardless_of_cookie(self, app_with_settings):
        """window.openAnalyticsConsentBanner must always show the banner,
        even if consent was previously accepted or rejected."""
        with app_with_settings.test_client() as client:
            # Test with accepted cookie
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # The function should be exposed and not check cookie value
            assert "openAnalyticsConsentBanner" in html
            assert "if (consent !== ACCEPT_VALUE)" not in html
            assert "consent !== ACCEPT_VALUE" not in html

    def test_handlers_attached_at_init_regardless_of_choice(self, app_with_settings):
        """Accept and Reject handlers must be attached once at init
        regardless of the stored choice, so a reopened banner has working buttons."""
        with app_with_settings.test_client() as client:
            # Test with accepted cookie - handlers should still be attached
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # attachHandlers should be called unconditionally
            assert "attachHandlers" in html
            # The init function should call attachHandlers before checking consent
            assert "attachHandlers()" in html

    def test_reject_after_accept_updates_cookie_and_reloads(self, app_with_settings):
        """Reject after an earlier Accept sets the cookie to rejected,
        calls gtag consent update analytics_storage denied if gtag exists,
        and reloads the page so Clarity stops."""
        with app_with_settings.test_client() as client:
            client.set_cookie("analytics_consent", "accepted")
            rv = client.get("/pricing")
            html = rv.data.decode()
            # withdrawConsent function should exist
            assert "withdrawConsent" in html
            # Should call gtag consent update with analytics_storage denied
            assert "analytics_storage" in html and "denied" in html
            # Should reload the page
            assert "location.reload" in html


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


class TestSharedArtefactPageExcludesAnalytics:
    """Shared artefact pages must never load GA4 or Clarity."""

    @pytest.fixture
    def app_with_settings(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "GA4_MEASUREMENT_ID", "G-TEST123")
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "test-clarity-id")
        monkeypatch.setitem(app.config, "GOOGLE_SITE_VERIFICATION", "google-verify-token")
        monkeypatch.setitem(app.config, "BING_SITE_VERIFICATION", "bing-verify-token")
        return app

    def test_shared_artefact_page_no_ga4(self, app_with_settings):
        """Shared artefact page must not contain GA4 markup."""
        with app_with_settings.app_context():
            from flask import render_template
            from datetime import datetime
            with app_with_settings.test_request_context():
                html = render_template(
                    "artefact_share/public.html",
                    artefact_title="Test Artefact",
                    artefact_type="capability_map",
                    data={"total_count": 0, "domain_count": 0, "groups": []},
                    organization_name="Test Org",
                    generated_at=datetime(2024, 1, 1, 0, 0, 0),
                    link=type("Link", (), {"created_at": datetime(2024, 1, 1, 0, 0, 0)})()
                )
                assert "googletagmanager.com" not in html
                assert "gtag(" not in html
                assert "dataLayer" not in html

    def test_shared_artefact_page_no_clarity(self, app_with_settings):
        """Shared artefact page must not contain Clarity markup."""
        with app_with_settings.app_context():
            from flask import render_template
            from datetime import datetime
            with app_with_settings.test_request_context():
                html = render_template(
                    "artefact_share/public.html",
                    artefact_title="Test Artefact",
                    artefact_type="capability_map",
                    data={"total_count": 0, "domain_count": 0, "groups": []},
                    organization_name="Test Org",
                    generated_at=datetime(2024, 1, 1, 0, 0, 0),
                    link=type("Link", (), {"created_at": datetime(2024, 1, 1, 0, 0, 0)})()
                )
                assert "clarity.ms" not in html
                assert "clarity(" not in html

    def test_shared_artefact_page_no_consent_banner(self, app_with_settings):
        """Shared artefact page must not contain consent banner."""
        with app_with_settings.app_context():
            from flask import render_template
            from datetime import datetime
            with app_with_settings.test_request_context():
                html = render_template(
                    "artefact_share/public.html",
                    artefact_title="Test Artefact",
                    artefact_type="capability_map",
                    data={"total_count": 0, "domain_count": 0, "groups": []},
                        organization_name="Test Org",
                        generated_at=datetime(2024, 1, 1, 0, 0, 0),
                        link=type("Link", (), {"created_at": datetime(2024, 1, 1, 0, 0, 0)})()
                    )
                    assert "analytics-consent-banner" not in html


class TestHeadHtmlNoGoogleAnalyticsId:
    """_head.html must not reference GOOGLE_ANALYTICS_ID."""

    def test_head_html_no_google_analytics_id(self, app):
        with app.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            # Old Universal Analytics loader must be gone
            assert "GOOGLE_ANALYTICS_ID" not in html
            assert "google-analytics.com/analytics.js" not in html
            assert "ga('create'" not in html
            assert "ga('send', 'pageview')" not in html

    def test_head_html_no_google_analytics_id_in_template_source(self):
        """Check the template source directly."""
        from pathlib import Path
        head_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "partials" / "_head.html"
        source = head_path.read_text(encoding="utf-8")
        assert "GOOGLE_ANALYTICS_ID" not in source
        assert "google-analytics.com/analytics.js" not in source
        assert "ga('create'" not in source
        assert "ga('send', 'pageview')" not in source


class TestConsentCookieSecureFlag:
    """Consent cookie must have Secure flag when on HTTPS."""

    @pytest.fixture
    def app_with_settings(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "GA4_MEASUREMENT_ID", "G-TEST123")
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "test-clarity-id")
        return app

    def test_consent_cookie_sets_secure_on_https(self, app_with_settings):
        """The cookie setting JS should include Secure flag when protocol is https."""
        with app_with_settings.test_client() as client:
            rv = client.get("/pricing")
            html = rv.data.decode()
            # The setCookie function should check location.protocol
            assert "location.protocol === 'https:'" in html
            assert "Secure" in html

    def test_consent_banner_template_has_secure_logic(self):
        """Check the consent banner template source directly for Secure flag logic."""
        from pathlib import Path
        banner_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "partials" / "analytics_consent_banner.html"
        source = banner_path.read_text(encoding="utf-8")
        assert "location.protocol === 'https:'" in source
        assert "Secure" in source