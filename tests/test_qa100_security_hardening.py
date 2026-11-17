"""Regression tests for QA sweep findings F-06, F-07, F-08 and V-06.

Written against the shared fixtures in ``tests/conftest.py`` (``db_session``
rolls everything back; ``app`` is session-scoped, so nothing here may register
a route or permanently mutate config).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _make_user(db_session, org, password="Correct-Horse-9!"):
    from app.models.user import User

    user = User(
        email=f"qa100-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id,
        confirmed=True,
    )
    if hasattr(type(user), "password"):
        user.password = password
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------------------
# F-08 - SSRF guard on server-fetched integration URLs
# --------------------------------------------------------------------------

class TestF08SsrfGuard:
    """The Salesforce ``instance_url`` reaches ``requests.post`` verbatim.

    ``validate_salesforce_instance_url`` is the control; these pin both halves
    of it - the host allow-list and the private/link-local address block.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",   # cloud metadata
            "https://169.254.169.254/",
            "http://127.0.0.1:5000/admin",                # loopback
            "https://localhost/",
            "http://10.0.0.5/",                           # RFC1918
            "https://192.168.1.1/",
            "http://[::1]/",                              # IPv6 loopback
            "file:///etc/passwd",                         # non-http scheme
            "https://evil.example.com/",                  # off allow-list
            "https://salesforce.com.attacker.test/",      # suffix confusion
            "https://user:pw@login.salesforce.com/",      # embedded credentials
        ],
    )
    def test_hostile_urls_are_rejected(self, url):
        from app.utils.ssrf_guard import (
            BlockedOutboundURL,
            validate_salesforce_instance_url,
        )

        with pytest.raises(BlockedOutboundURL):
            validate_salesforce_instance_url(url)

    def test_private_ranges_blocked_even_without_an_allow_list(self):
        from app.utils.ssrf_guard import BlockedOutboundURL, validate_outbound_url

        with pytest.raises(BlockedOutboundURL):
            validate_outbound_url("https://169.254.169.254/", allowed_host_suffixes=())

    def test_get_token_refuses_a_blocked_host_without_fetching(self, monkeypatch):
        """The guard must run BEFORE requests.post, not after."""
        from app.modules.solutions_strategic.v2.services import (
            salesforce_discovery_service as svc,
        )

        called = []

        def _boom(*args, **kwargs):
            called.append(args)
            raise AssertionError("requests.post must not be reached")

        monkeypatch.setattr(svc.requests, "post", _boom)

        result = svc.SalesforceDiscoveryService._get_token(
            "http://169.254.169.254", "cid", "csecret"
        )
        assert result is None
        assert called == []

    def test_query_refuses_a_blocked_host_without_fetching(self, monkeypatch):
        from app.modules.solutions_strategic.v2.services import (
            salesforce_discovery_service as svc,
        )

        def _boom(*args, **kwargs):
            raise AssertionError("requests.get must not be reached")

        monkeypatch.setattr(svc.requests, "get", _boom)

        assert (
            svc.SalesforceDiscoveryService._query(
                "http://127.0.0.1", "tok", "SELECT Id FROM AppDefinition"
            )
            == []
        )


# --------------------------------------------------------------------------
# F-07 - server-authoritative session idle timeout
# --------------------------------------------------------------------------

class TestF07SessionIdleTimeout:
    def test_idle_timeout_is_configured_and_distinct_from_absolute_lifetime(self, app):
        """The 8-hour cap is absolute; the idle window must be separate, shorter."""
        idle = app.config.get("SESSION_IDLE_TIMEOUT_SECONDS")
        assert idle, "SESSION_IDLE_TIMEOUT_SECONDS not installed"
        absolute = app.config["PERMANENT_SESSION_LIFETIME"]
        assert idle < absolute.total_seconds()

    def test_hook_is_registered(self, app):
        # Renamed by session-invalidation-on-logout: the hook now also
        # enforces server-side session revocation, not idle timeout alone.
        names = [f.__name__ for f in app.before_request_funcs.get(None, [])]
        assert "_enforce_session_policy" in names

    def test_stale_session_is_torn_down_server_side(
        self, app, db_session, make_org, login_as
    ):
        """A session whose last activity is older than the window is invalidated.

        This is the point of the finding: enforcement must not depend on the
        page's JavaScript, so it is asserted through a bare test client.
        """
        from app._bootstrap.session_policy import LAST_ACTIVITY_KEY

        org = make_org("idle")
        user = _make_user(db_session, org)

        idle = app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
        client = app.test_client()
        login_as(client, user)

        stale = int(
            (datetime.now(timezone.utc) - timedelta(seconds=idle + 60)).timestamp()
        )
        with client.session_transaction() as sess:
            sess[LAST_ACTIVITY_KEY] = stale

        resp = client.get("/account/manage")
        assert resp.status_code in (302, 401), resp.status_code
        if resp.status_code == 302:
            assert "login" in resp.headers["Location"]

        with client.session_transaction() as sess:
            assert "_user_id" not in sess, "session was not cleared"

    def test_active_session_is_not_torn_down(
        self, app, db_session, make_org, login_as
    ):
        from app._bootstrap.session_policy import LAST_ACTIVITY_KEY

        org = make_org("idleok")
        user = _make_user(db_session, org)

        client = app.test_client()
        login_as(client, user)
        with client.session_transaction() as sess:
            sess[LAST_ACTIVITY_KEY] = int(datetime.now(timezone.utc).timestamp())

        resp = client.get("/account/manage")
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert "_user_id" in sess

    def test_health_does_not_refresh_the_idle_stamp(
        self, app, db_session, make_org, login_as
    ):
        """A background /health poll must not keep an abandoned tab alive."""
        from app._bootstrap.session_policy import LAST_ACTIVITY_KEY

        org = make_org("idlehealth")
        user = _make_user(db_session, org)
        client = app.test_client()
        login_as(client, user)

        old = int(datetime.now(timezone.utc).timestamp()) - 120
        with client.session_transaction() as sess:
            sess[LAST_ACTIVITY_KEY] = old

        client.get("/health")

        with client.session_transaction() as sess:
            assert sess.get(LAST_ACTIVITY_KEY) == old


# --------------------------------------------------------------------------
# F-06 - rate limiting
# --------------------------------------------------------------------------

class TestF06RateLimiting:
    def test_limiter_is_installed_with_global_defaults(self, app):
        from app._bootstrap import rate_limiting

        assert rate_limiting.limiter is not None, "Flask-Limiter not installed"
        assert rate_limiting._DEFAULT_LIMITS
        assert rate_limiting._WRITE_LIMITS

    def test_health_and_csp_report_are_exempt(self):
        from app._bootstrap.rate_limiting import _EXEMPT_ENDPOINTS

        assert "global_health_check" in _EXEMPT_ENDPOINTS
        assert "csp_report" in _EXEMPT_ENDPOINTS

    def test_flood_eventually_gets_429_with_retry_after(self, app):
        """The audited behaviour was 40 rapid requests, 40 x 200, no 429."""
        from app._bootstrap import rate_limiting

        limiter = rate_limiting.limiter
        assert limiter is not None

        was_enabled = app.config.get("RATE_LIMITING_ENABLED")
        app.config["RATE_LIMITING_ENABLED"] = True
        try:
            client = app.test_client()
            codes = [client.get("/account/login").status_code for _ in range(200)]
            assert 429 in codes, "no throttle after 200 rapid requests"
            resp = client.get("/account/login")
            assert resp.status_code == 429
            assert resp.headers.get("Retry-After")
        finally:
            app.config["RATE_LIMITING_ENABLED"] = was_enabled

    def test_write_bucket_is_tighter_than_the_read_bucket(self):
        from app._bootstrap.rate_limiting import _DEFAULT_LIMITS, _WRITE_LIMITS

        def _per_minute(limits):
            for item in limits:
                if "minute" in item:
                    return int(item.split()[0])
            raise AssertionError("no per-minute limit declared")

        assert _per_minute(_WRITE_LIMITS) < _per_minute(_DEFAULT_LIMITS)


# --------------------------------------------------------------------------
# V-06 - authentication events land in the SURFACED audit log
# --------------------------------------------------------------------------

class TestV06AuthAuditing:
    def test_login_success_writes_to_the_table_admin_audit_log_reads(
        self, app, db_session, make_org
    ):
        """The pre-existing audit_logger wrote to ``audit_events``, which nothing
        surfaces. The event must land in ``soc2_audit_log``."""
        from app.models.audit_log import AuditLog
        from app.services import auth_audit

        org = make_org("authaudit")
        user = _make_user(db_session, org)

        with app.test_request_context(
            "/account/login",
            environ_base={"REMOTE_ADDR": "203.0.113.9", "HTTP_USER_AGENT": "QA/1.0"},
        ):
            entry = auth_audit.record_login_success(user)

        assert entry is not None
        assert entry.__tablename__ == "soc2_audit_log"
        assert entry.action == "login"
        assert entry.table_name == "auth"
        assert entry.user_id == user.id
        assert entry.ip_address == "203.0.113.9"
        assert entry.user_agent == "QA/1.0"
        assert entry.created_at is not None
        assert AuditLog.query.filter_by(id=entry.id).first() is not None

    def test_failed_login_is_recorded_and_attributed(self, app, db_session, make_org):
        from app.services import auth_audit

        org = make_org("authfail")
        user = _make_user(db_session, org)

        with app.test_request_context(
            "/account/login", environ_base={"REMOTE_ADDR": "198.51.100.4"}
        ):
            entry = auth_audit.record_login_failure(user.email)

        assert entry is not None
        assert entry.action == "login_failed"
        assert entry.user_id == user.id, "failed attempt not attributed to the account"
        assert entry.ip_address == "198.51.100.4"

    def test_failed_login_for_unknown_email_still_recorded(self, app, db_session):
        from app.services import auth_audit

        with app.test_request_context("/account/login"):
            entry = auth_audit.record_login_failure("nobody-here@example.invalid")

        assert entry is not None
        assert entry.user_id is None
        assert entry.new_value["attempted_email"] == "nobody-here@example.invalid"

    def test_last_login_reads_back_and_excludes_the_current_entry(
        self, app, db_session, make_org
    ):
        from app.services import auth_audit

        org = make_org("lastlogin")
        user = _make_user(db_session, org)

        with app.test_request_context(
            "/account/login", environ_base={"REMOTE_ADDR": "203.0.113.1"}
        ):
            first = auth_audit.record_login_success(user)
        with app.test_request_context(
            "/account/login", environ_base={"REMOTE_ADDR": "203.0.113.2"}
        ):
            second = auth_audit.record_login_success(user)

        prev = auth_audit.last_login(user.id, before_id=second.id)
        assert prev is not None and prev.id == first.id
        assert prev.ip_address == "203.0.113.1"

    def test_last_login_is_none_for_a_user_who_never_logged_in(
        self, app, db_session, make_org
    ):
        """No prior login must yield None so the page renders an em dash - never
        a fabricated value."""
        from app.services import auth_audit

        org = make_org("nologin")
        user = _make_user(db_session, org)
        assert auth_audit.last_login(user.id) is None

    def test_account_page_no_longer_claims_tracking_is_unavailable(self):
        from pathlib import Path

        tpl = (
            Path(__file__).resolve().parents[1]
            / "app" / "templates" / "account" / "manage.html"
        ).read_text(encoding="utf-8")
        assert "Last login tracking not available" not in tpl
        assert "IP tracking not available" not in tpl
        assert "last_login_entry" in tpl


# --------------------------------------------------------------------------
# ARCH-070 - CSP style-src
# --------------------------------------------------------------------------

class TestArch070Csp:
    def _prod_csp(self, app):
        """Render the production branch of the policy."""
        debug_was = app.debug
        app.debug = False
        try:
            return app.test_client().get("/health").headers[
                "Content-Security-Policy"
            ]
        finally:
            app.debug = debug_was

    def test_style_src_no_longer_carries_blanket_unsafe_inline(self, app):
        csp = self._prod_csp(app)
        directives = {
            d.strip().split(" ")[0]: d.strip() for d in csp.split(";") if d.strip()
        }
        assert "unsafe-inline" not in directives["style-src"]
        assert "unsafe-inline" not in directives["style-src-elem"]
        assert "nonce-" in directives["style-src-elem"]
        # Attributes remain allowed, explicitly and visibly.
        assert directives["style-src-attr"] == "style-src-attr 'unsafe-inline'"

    def test_script_src_still_has_a_nonce_and_no_unsafe_inline(self, app):
        """Guard the already-fixed half against regression."""
        csp = self._prod_csp(app)
        script_src = [
            d for d in csp.split(";") if d.strip().startswith("script-src")
        ][0]
        assert "nonce-" in script_src
        assert "'unsafe-inline'" not in script_src

    def test_template_style_blocks_are_nonced_at_compile_time(self, app):
        from app._bootstrap.security import CspNonceExtension

        ext = CspNonceExtension(app.jinja_env)
        out = ext.preprocess("<style>body{color:red}</style>", "t.html")
        assert 'nonce="{{ csp_nonce }}"' in out
        # Idempotent - re-processing must not stack attributes.
        assert ext.preprocess(out, "t.html").count("nonce=") == 1
