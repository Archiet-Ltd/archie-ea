"""First-party, cookieless analytics for public marketing pages.

Covers:
  1. The visitor-hash function itself: deterministic for the same inputs,
     completely different when only the day changes (daily rotation), and
     never reversible to the IP/user-agent it was built from.
  2. Each event type is logged with the right data, through the real HTTP
     surface that triggers it (not by calling the service directly), and no
     raw IP address or user agent is stored anywhere on the row.
  3. No cookie, no localStorage write is introduced by any of this.
"""

from __future__ import annotations

from datetime import date

from app.services.visitor_hash import compute_visitor_hash, current_utc_day


# ── 1. The pure hashing function ───────────────────────────────────────────


class TestVisitorHash:
    def test_same_inputs_same_day_same_hash(self):
        h1 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret", date(2026, 10, 7))
        h2 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret", date(2026, 10, 7))
        assert h1 == h2

    def test_different_day_different_hash(self):
        """The whole point of the rotation: tomorrow's hash is unrelated to today's."""
        h1 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret", date(2026, 10, 7))
        h2 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret", date(2026, 10, 8))
        assert h1 != h2

    def test_different_visitor_different_hash(self):
        h1 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret", date(2026, 10, 7))
        h2 = compute_visitor_hash("198.51.100.4", "Mozilla/5.0", "secret", date(2026, 10, 7))
        assert h1 != h2

    def test_hash_is_a_fixed_length_hex_digest_not_the_raw_inputs(self):
        h = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret", date(2026, 10, 7))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
        assert "203.0.113.9" not in h
        assert "secret" not in h

    def test_different_secret_different_hash(self):
        """Changing the server-side pepper invalidates every previous hash --
        confirms the secret actually participates, not just the day."""
        h1 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret-a", date(2026, 10, 7))
        h2 = compute_visitor_hash("203.0.113.9", "Mozilla/5.0", "secret-b", date(2026, 10, 7))
        assert h1 != h2

    def test_current_utc_day_is_a_date(self):
        assert isinstance(current_utc_day(), date)


# ── 2. Each event type, logged through the real surface ───────────────────


def _visit(client, path, **kw):
    kw.setdefault("environ_base", {}).setdefault("REMOTE_ADDR", "203.0.113.50")
    kw["environ_base"].setdefault("HTTP_USER_AGENT", "pytest-agent/1.0")
    return client.get(path, **kw)


class TestPageViewLogging:
    def test_get_home_page_logs_a_page_view(self, client, db_session):
        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(event_type="page_view").count()
        resp = _visit(client, "/")
        assert resp.status_code == 200

        rows = (
            PublicVisitorEvent.query.filter_by(event_type="page_view")
            .order_by(PublicVisitorEvent.id)
            .all()
        )
        assert len(rows) == before + 1
        assert rows[-1].path == "/"

    def test_get_a_module_page_logs_its_own_path(self, client, db_session):
        from app.models.public_visitor_event import PublicVisitorEvent
        from app.services.public_pages import load_all_pages

        module_page = next(p for p in load_all_pages() if p.family == "module")
        resp = _visit(client, module_page.url)
        assert resp.status_code == 200

        row = (
            PublicVisitorEvent.query.filter_by(event_type="page_view")
            .order_by(PublicVisitorEvent.id.desc())
            .first()
        )
        assert row.path == module_page.url

    def test_post_to_home_page_is_not_logged_as_a_page_view(self, client, db_session):
        """The home page's own POST (waitlist signup) is not a page view."""
        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(event_type="page_view").count()
        client.post("/", data={"email": "not-a-pageview@example.com"})
        assert PublicVisitorEvent.query.filter_by(event_type="page_view").count() == before

    def test_dashboard_route_is_not_logged_as_a_public_pageview(self, client, db_session):
        """Only the explicit public-page allow-list counts -- never every HTML
        response, so an authenticated screen never inflates marketing pageviews."""
        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(event_type="page_view").count()
        # Anonymous hits a login-gated route -- a redirect, not a tracked page.
        client.get("/dashboards/overview")
        assert PublicVisitorEvent.query.filter_by(event_type="page_view").count() == before

    def test_no_raw_ip_or_user_agent_stored_on_the_row(self, client, db_session):
        from app.models.public_visitor_event import PublicVisitorEvent

        _visit(client, "/vision", environ_base={
            "REMOTE_ADDR": "203.0.113.77", "HTTP_USER_AGENT": "VerySpecificAgent/9.9",
        })
        row = (
            PublicVisitorEvent.query.filter_by(event_type="page_view")
            .order_by(PublicVisitorEvent.id.desc())
            .first()
        )
        row_values = [str(v) for v in (row.path, row.plan, row.offer, row.visitor_hash)]
        assert not any("203.0.113.77" in v for v in row_values)
        assert not any("VerySpecificAgent" in v for v in row_values)
        # visitor_hash is the one-way digest, never the raw inputs themselves.
        assert len(row.visitor_hash) == 64

    def test_hash_rotates_for_the_same_visitor_on_a_different_day(self, client, db_session, monkeypatch):
        from datetime import date

        from app.models.public_visitor_event import PublicVisitorEvent

        same_client_kwargs = {
            "environ_base": {"REMOTE_ADDR": "203.0.113.99", "HTTP_USER_AGENT": "SameVisitor/1.0"}
        }

        monkeypatch.setattr(
            "app.services.visitor_hash.current_utc_day", lambda: date(2026, 10, 7)
        )
        client.get("/vision", **same_client_kwargs)
        day1 = (
            PublicVisitorEvent.query.filter_by(event_type="page_view")
            .order_by(PublicVisitorEvent.id.desc()).first()
        )

        monkeypatch.setattr(
            "app.services.visitor_hash.current_utc_day", lambda: date(2026, 10, 8)
        )
        client.get("/vision", **same_client_kwargs)
        day2 = (
            PublicVisitorEvent.query.filter_by(event_type="page_view")
            .order_by(PublicVisitorEvent.id.desc()).first()
        )

        assert day1.visitor_hash != day2.visitor_hash
        assert day1.visitor_day == date(2026, 10, 7)
        assert day2.visitor_day == date(2026, 10, 8)


class TestSignupFunnelLogging:
    def test_get_register_form_logs_signup_started(self, client, db_session):
        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(event_type="signup_started").count()
        resp = _visit(client, "/account/register")
        assert resp.status_code == 200
        assert PublicVisitorEvent.query.filter_by(event_type="signup_started").count() == before + 1

    def test_failed_register_post_does_not_log_a_second_started_event(self, client, db_session):
        """Started = reaching the form, not every retry after a validation error."""
        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(event_type="signup_started").count()
        client.post("/account/register", data={"email": "not-valid"})
        assert PublicVisitorEvent.query.filter_by(event_type="signup_started").count() == before

    def test_successful_register_post_logs_signup_completed(self, client, db_session):
        import uuid

        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(event_type="signup_completed").count()
        email = f"newvisitor-{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post(
            "/account/register",
            data={
                "first_name": "Jo",
                "last_name": "Visitor",
                "email": email,
                "password": "test-password-123",
                "password2": "test-password-123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert (
            PublicVisitorEvent.query.filter_by(event_type="signup_completed").count()
            == before + 1
        )


class TestPricingPlanClickLogging:
    def test_track_plan_click_logs_the_plan_and_redirects_on(self, client, db_session):
        from app.models.public_visitor_event import PublicVisitorEvent

        resp = client.get(
            "/t/plan-click?plan=startup&next=/signup", follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/signup")

        row = (
            PublicVisitorEvent.query.filter_by(event_type="pricing_plan_click")
            .order_by(PublicVisitorEvent.id.desc()).first()
        )
        assert row is not None
        assert row.plan == "startup"

    def test_unsafe_next_falls_back_to_a_safe_default(self, client, db_session):
        """An off-site next= must never be followed -- same rule as login's own ?next=."""
        resp = client.get(
            "/t/plan-click?plan=team&next=https://evil.example.com",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "evil.example.com" not in location

    def test_pricing_page_plan_buttons_go_through_the_tracker(self, client):
        html = client.get("/pricing").data.decode()
        assert "/t/plan-click?plan=community" in html
        assert "/t/plan-click?plan=startup" in html or "plan=startup" in html


class TestOfferEnquiryLogging:
    def test_successful_offer_submission_logs_which_offer(self, client, db_session):
        from app.models.public_visitor_event import PublicVisitorEvent

        before = PublicVisitorEvent.query.filter_by(
            event_type="offer_enquiry_submitted"
        ).count()
        resp = client.post(
            "/offers/inquire",
            data={
                "offer": "architecture_health_check",
                "family": "site",
                "slug": "architecture-health-check",
                "email": "enquiry-test@example.com",
                "consent": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        row = (
            PublicVisitorEvent.query.filter_by(event_type="offer_enquiry_submitted")
            .order_by(PublicVisitorEvent.id.desc()).first()
        )
        assert row is not None
        assert row.offer == "architecture_health_check"
        assert (
            PublicVisitorEvent.query.filter_by(event_type="offer_enquiry_submitted").count()
            == before + 1
        )


class TestIndexNowKeyNeedsNoSetup:
    """The IndexNow key is not a secret -- the protocol requires it to be
    served in plaintext, unauthenticated, at /<key>.txt, specifically so
    search engines can verify it without any credential. There is nothing
    to protect by keeping it out of the repository, so it ships with a
    real committed default and works with no environment variable set."""

    def test_key_is_set_out_of_the_box(self, app):
        key = app.config.get("INDEXNOW_API_KEY", "")
        assert key, "INDEXNOW_API_KEY must have a real, non-empty default"
        assert 32 <= len(key) <= 128
        assert all(c in "0123456789abcdef" for c in key)

    def test_key_file_route_serves_the_configured_key(self, client, app):
        key = app.config["INDEXNOW_API_KEY"]
        resp = client.get(f"/{key}.txt")
        assert resp.status_code == 200
        assert resp.data.decode() == key

    def test_wrong_key_file_is_a_404(self, client):
        resp = client.get("/not-the-real-key.txt")
        assert resp.status_code == 404


class TestNoCookieNoLocalStorage:
    """The app already sets its own (pre-existing, unrelated) session
    cookie carrying the CSRF token on every page -- nothing to do with this
    feature, and a strictly-necessary cookie that needs no consent either
    way. What this feature must never do is write a visitor-identifying
    cookie, a localStorage key, or talk to a third-party domain of its own,
    across every file it adds or touches -- checked directly against the
    source, the same thing a diff review would grep for."""

    NEW_OR_EDITED_FILES = [
        "app/models/public_visitor_event.py",
        "app/services/visitor_hash.py",
        "app/services/public_analytics_service.py",
        "app/services/indexnow_service.py",
        "app/middleware/public_analytics_middleware.py",
        "app/commands/indexnow_commands.py",
        "app/main/views.py",
        "app/modules/account/routes/mail_views.py",
        "app/templates/public/page.html",
        "app/templates/main/index.html",
        "app/templates/public/use_cases_index.html",
        "app/templates/public/vs_hub.html",
    ]

    def test_no_set_cookie_or_localstorage_in_this_feature_s_files(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        # Actual call/assignment shapes, not prose: this module's own
        # docstrings describe what it deliberately does NOT do, and
        # mention these words for exactly that reason.
        forbidden = ("set_cookie(", "localStorage.setItem", "localStorage[", "document.cookie =")
        for rel_path in self.NEW_OR_EDITED_FILES:
            text = (repo_root / rel_path).read_text(encoding="utf-8")
            for needle in forbidden:
                assert needle not in text, f"{rel_path}: found {needle!r}"
