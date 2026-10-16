"""Usage tracking is on by default: one page-view row per module-directory
endpoint, keyed by endpoint name, with no personal data.

Per-organisation counts are a later change: `UsageAnalytics` carries no
`organization_id` column today, so `/usage-analytics/api/summary` reports
across every tenant. This module only pins that the page itself stays behind
`@login_required` and records the gap below rather than papering over it with
a scoped assertion the code does not actually make.
"""
import pytest

from app import db
from app.models.usage_analytics import UsageAnalytics
from app.models.user import User


def _user(org, tag, **overrides):
    kwargs = dict(
        email=f"usage-track-{tag}-{org.id}@example.com",
        first_name="Usage",
        last_name="Tracker",
        organization_id=org.id,
        confirmed=True,  # else before_request bounces to /account/unconfirmed
    )
    kwargs.update(overrides)
    u = User(**kwargs)
    u.password = "TestPass123!"
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def analytics_on(app):
    """Flip ENABLE_USAGE_ANALYTICS on for the duration of one test, then
    restore TestingConfig's off-by-default value. The middleware hooks read
    this key from `current_app.config` at request time, so no second
    `create_app()` is needed."""
    original = app.config.get("ENABLE_USAGE_ANALYTICS")
    app.config["ENABLE_USAGE_ANALYTICS"] = True
    try:
        yield
    finally:
        app.config["ENABLE_USAGE_ANALYTICS"] = original


def test_directory_endpoint_writes_one_anonymous_row(
    db_session, make_org, client, login_as, analytics_on
):
    org = make_org("usage-directory")
    user = _user(org, "directory")
    login_as(client, user)

    before = UsageAnalytics.query.count()
    resp = client.get("/usage-analytics/")
    assert resp.status_code in (200, 302)

    rows = UsageAnalytics.query.order_by(UsageAnalytics.id.desc()).all()
    assert len(rows) == before + 1
    row = rows[0]
    assert row.feature_name == "usage_analytics.analytics_root"
    assert row.event_type == "page_view"
    assert row.user_id == user.id
    assert row.ip_address is None
    assert row.user_agent is None
    assert row.referrer is None


def test_non_directory_endpoint_writes_nothing(
    db_session, make_org, client, login_as, analytics_on
):
    org = make_org("usage-non-directory")
    user = _user(org, "nondirectory")
    login_as(client, user)

    before = UsageAnalytics.query.count()
    # An API route under the same blueprint, but not itself a module-directory
    # link (only usage_analytics.analytics_root is listed there).
    resp = client.get("/usage-analytics/api/summary")
    assert resp.status_code == 200

    after = UsageAnalytics.query.count()
    assert after == before


def test_flag_off_writes_nothing(db_session, make_org, client, login_as, app):
    original = app.config.get("ENABLE_USAGE_ANALYTICS")
    app.config["ENABLE_USAGE_ANALYTICS"] = False
    try:
        org = make_org("usage-flag-off")
        user = _user(org, "flagoff")
        login_as(client, user)

        before = UsageAnalytics.query.count()
        resp = client.get("/usage-analytics/")
        assert resp.status_code in (200, 302)

        after = UsageAnalytics.query.count()
        assert after == before
    finally:
        app.config["ENABLE_USAGE_ANALYTICS"] = original


def test_anonymous_request_writes_nothing(db_session, make_org, client, analytics_on):
    """An anonymous visitor is not usage anyone consented to being counted
    as; the redirect to login must not write a row or mint a session id."""
    before = UsageAnalytics.query.count()
    resp = client.get("/usage-analytics/dashboard")
    assert resp.status_code in (302, 401)

    after = UsageAnalytics.query.count()
    assert after == before


def test_denied_response_writes_nothing(db_session, make_org, client, login_as, analytics_on):
    """A signed-in but unauthorised request (403) is a denial, not a page
    view. main.settings is a directory link and @admin_required; this user
    holds no role, so it 403s."""
    org = make_org("usage-denied")
    user = _user(org, "denied")  # no role, not an admin
    login_as(client, user)

    before = UsageAnalytics.query.count()
    resp = client.get("/settings")
    assert resp.status_code == 403

    after = UsageAnalytics.query.count()
    assert after == before


def test_api_events_does_not_return_session_id_or_exception_text(
    db_session, make_org, client, login_as, analytics_on
):
    org = make_org("usage-events-sanitised")
    user = _user(org, "events-sanitised")

    UsageAnalytics.track_event(
        event_type="error_occurred",
        feature_name="usage_analytics.analytics_root",
        route_path="/usage-analytics/",
        user_id=user.id,
        session_id="a-session-id-that-must-not-leak",
        event_metadata={
            "error_type": "ValueError",
            "error_message": "a stack-trace-derived exception message",
            "method": "GET",
        },
    )
    db.session.flush()

    login_as(client, user)
    resp = client.get("/usage-analytics/api/events")
    assert resp.status_code == 200
    events = resp.get_json()
    assert events, "expected at least the seeded event back"
    for event in events:
        assert "session_id" not in event
        metadata = event.get("event_metadata") or {}
        assert "error_message" not in metadata


def test_config_flag_defaults():
    from config import Config, TestingConfig

    assert Config.ENABLE_USAGE_ANALYTICS is True
    assert TestingConfig.ENABLE_USAGE_ANALYTICS is False


def test_opted_out_user_writes_nothing(
    db_session, make_org, client, login_as, analytics_on
):
    org = make_org("usage-opt-out")
    user = _user(org, "optout")
    user.notification_preferences = {"analytics_opt_out": True}
    db.session.add(user)
    db.session.flush()
    login_as(client, user)

    before = UsageAnalytics.query.count()
    resp = client.get("/usage-analytics/")
    assert resp.status_code in (200, 302)

    after = UsageAnalytics.query.count()
    assert after == before


def test_analytics_opt_out_route_available_to_any_signed_in_user(
    db_session, make_org, client, login_as, analytics_on
):
    org = make_org("usage-opt-out-route")
    user = _user(org, "opt-out-route")  # no role, no admin flags
    login_as(client, user)

    resp = client.post("/settings/analytics-opt-out", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.get_json()["analytics_opt_out"] is True

    db.session.refresh(user)
    assert user.get_notification_preference("analytics_opt_out") is True

    login_as(client, user)
    before = UsageAnalytics.query.count()
    client.get("/usage-analytics/")
    after = UsageAnalytics.query.count()
    assert after == before

    # Turning it back on re-enables tracking.
    login_as(client, user)
    resp = client.post("/settings/analytics-opt-out", json={"enabled": True})
    assert resp.get_json()["analytics_opt_out"] is False
    db.session.refresh(user)
    assert user.get_notification_preference("analytics_opt_out") is False


def test_notification_preferences_save_preserves_analytics_opt_out(db_session, make_org):
    """account_routes.py's save_notification_preferences (both the v1 and v2
    blueprints) always rebuilds its own five-key dict and calls this same
    setter -- it must not silently turn tracking back on for a user who
    opted out through Settings. Exercised directly against the model, since
    both account routes share this one setter; proving the setter preserves
    the key proves both callers do too."""
    org = make_org("usage-preserve")
    user = _user(org, "preserve")
    user.set_notification_preferences({"analytics_opt_out": True})
    db.session.flush()

    # The account page's own save: five keys, no knowledge of analytics_opt_out.
    user.set_notification_preferences({
        "arb_decisions": False,
        "solution_updates": True,
        "assignment_changes": True,
        "weekly_digest": True,
        "mention_notifications": True,
    })
    db.session.flush()

    assert user.get_notification_preference("analytics_opt_out") is True
    assert user.get_notification_preference("arb_decisions") is False


def test_summary_api_counts_rows_by_feature_name(
    db_session, make_org, client, login_as, analytics_on
):
    org = make_org("usage-summary")
    user = _user(org, "summary")
    login_as(client, user)

    client.get("/usage-analytics/")
    client.get("/usage-analytics/")

    resp = client.get("/usage-analytics/api/summary")
    assert resp.status_code == 200
    summary = resp.get_json()
    assert summary["usage_analytics.analytics_root"]["total_events"] >= 2


def test_two_organisations_share_one_untenanted_summary(
    db_session, make_org, client, login_as, analytics_on
):
    """UsageAnalytics carries no organization_id column, so the summary API
    is not tenant-scoped: it reports across every organisation, not just the
    caller's own. Two real organisations, two real signed-in users, pin that
    plainly rather than leaving it as an assertion about @login_required
    alone. Per-organisation counts are a later change, tracked separately."""
    org_a = make_org("usage-org-a")
    org_b = make_org("usage-org-b")
    user_a = _user(org_a, "org-a")
    user_b = _user(org_b, "org-b")

    login_as(client, user_a)
    client.get("/usage-analytics/")
    login_as(client, user_b)
    client.get("/usage-analytics/")

    resp = client.get("/usage-analytics/api/summary")
    assert resp.status_code == 200
    root = resp.get_json()["usage_analytics.analytics_root"]
    assert root["total_events"] >= 2
    assert root["unique_users"] >= 2


def test_dashboard_renders_privacy_sentence(db_session, make_org, client, login_as):
    org = make_org("usage-privacy-sentence")
    user = _user(org, "privacy-sentence")
    login_as(client, user)

    resp = client.get("/usage-analytics/dashboard")
    assert resp.status_code == 200
    assert (
        b"Collect anonymous usage analytics to improve the application"
        in resp.data
    )


def test_dashboard_requires_login(client):
    resp = client.get("/usage-analytics/dashboard")
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert "login" in resp.headers.get("Location", "").lower()


def test_directory_endpoints_computed_from_directory_module(app):
    """`_compute_directory_endpoints` is what `init_app` calls once, at
    install time; calling it standalone here (not `init_app`, which would
    register a second set of hooks on the shared session app) proves it
    reads the real directory rather than a copy."""
    from app.middleware.partial_features_analytics import PartialFeaturesAnalytics

    mw = PartialFeaturesAnalytics()
    assert mw._directory_endpoints == frozenset()  # nothing computed before install

    with app.app_context():
        endpoints = mw._compute_directory_endpoints()
    assert isinstance(endpoints, frozenset)
    assert "usage_analytics.analytics_root" in endpoints


def test_response_time_is_a_real_duration_not_epoch_time():
    """g.analytics_start_time used to be read from a WSGI environ key Flask
    never sets ('REQUEST_TIME'), so the "elapsed" time was actually
    time.time() - 0: the epoch in milliseconds, roughly 1.7 trillion. This
    pins that a real short interval now comes out as a real short interval."""
    import time as time_module

    from flask import g

    from app.middleware.partial_features_analytics import PartialFeaturesAnalytics

    mw = PartialFeaturesAnalytics()
    from flask import Flask

    scratch_app = Flask(__name__)
    with scratch_app.test_request_context("/"):
        g.analytics_start_time = time_module.time() - 0.05
        elapsed_ms = mw._calculate_response_time()
    assert 0 <= elapsed_ms < 5000
