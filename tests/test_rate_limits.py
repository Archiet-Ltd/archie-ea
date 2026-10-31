"""Rate limits an administrator can see and set: shared counters, allow and deny lists,
the violation log and its scoping, and what a refused request looks like."""

import uuid
from datetime import datetime, timedelta

import pytest
from limits import parse, strategies
from sqlalchemy import create_engine, text


@pytest.fixture
def clean(app):
    """Committed state these tests create is removed before and after."""
    from app import db
    from app.services import rate_limiter

    def wipe():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM rate_limit_rules"))
            connection.execute(text("DELETE FROM rate_limit_counters"))
            connection.execute(text("DELETE FROM rate_limit_violations"))
        rate_limiter._rules_cache["version"] = None

    with app.app_context():
        wipe()
        yield rate_limiter
        wipe()


# ---- one limit shared by two workers ----------------------------------------------------------

def test_two_workers_count_one_limit(app, clean):
    """Two storages on two separate connection pools (as two gunicorn workers have) share a limit of 5."""
    from app import db

    url = db.engine.url
    engine_a = create_engine(url)
    engine_b = create_engine(url)
    try:
        worker_a = strategies.FixedWindowRateLimiter(clean.DatabaseStorage(engine=engine_a))
        worker_b = strategies.FixedWindowRateLimiter(clean.DatabaseStorage(engine=engine_b))
        item = parse("5 per minute")
        key = "ip:198.51.100.%d" % (uuid.uuid4().int % 200)
        results = []
        for turn in range(8):
            worker = worker_a if turn % 2 == 0 else worker_b
            results.append(worker.hit(item, key))
        assert results == [True] * 5 + [False] * 3, results
        # Either worker reports the same allowance left.
        assert worker_a.get_window_stats(item, key).remaining == 0
        assert worker_b.get_window_stats(item, key).remaining == 0
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_reset_clears_the_count_for_a_named_key(app, clean):
    limiter = clean.RateLimiter()
    key = "reset-walk-%s" % uuid.uuid4().hex[:8]
    assert limiter.check_rate_limit(key, 2, 60) == (True, None)
    assert limiter.check_rate_limit(key, 2, 60) == (True, None)
    allowed, retry_after = limiter.check_rate_limit(key, 2, 60)
    assert allowed is False and retry_after

    limiter.reset(key)

    assert limiter.check_rate_limit(key, 2, 60) == (True, None)


def test_a_finished_window_starts_again(app, clean):
    storage = clean.DatabaseStorage()
    assert storage.incr("k:window", 60) == 1
    assert storage.incr("k:window", 60) == 2
    with app.app_context():
        from app import db

        with db.engine.begin() as connection:
            connection.execute(text("UPDATE rate_limit_counters SET expires_at = 0 WHERE key = 'k:window'"))
    assert storage.get("k:window") == 0
    assert storage.incr("k:window", 60) == 1


# ---- allow and deny lists ----------------------------------------------------------------------

def test_deny_wins_over_allow(clean):
    clean.add_rule("allow", "address", "10.0.0.0/8")
    clean.add_rule("deny", "address", "10.1.2.3")
    assert clean.access_verdict("10.1.2.3")[0] == "deny"
    assert clean.access_verdict("10.9.9.9")[0] == "allow"
    assert clean.access_verdict("11.0.0.1") == (None, None)
    assert clean.access_verdict(None) == (None, None)
    assert clean.access_verdict("not an address") == (None, None)


def test_a_range_matches_its_members_and_an_ipv6_address_never_matches_an_ipv4_range(clean):
    clean.add_rule("deny", "address", "203.0.113.0/24")
    assert clean.access_verdict("203.0.113.200")[0] == "deny"
    assert clean.access_verdict("203.0.114.1")[0] is None
    assert clean.access_verdict("2001:db8::1")[0] is None


def test_a_user_entry_is_found_by_email_and_matches_that_user_only(app, clean):
    from app import db
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(email="limits.%s@example.com" % suffix, first_name="L", last_name="U", confirmed=True)
    user.password = "pw-%s" % suffix
    db.session.add(user)
    db.session.commit()
    try:
        clean.add_rule("deny", "user", "LIMITS.%s@example.com" % suffix)
        assert clean.access_verdict("198.51.100.1", user.id)[0] == "deny"
        assert clean.access_verdict("198.51.100.1", user.id + 1000)[0] is None
        assert clean.access_verdict("198.51.100.1", None)[0] is None
        listed = clean.list_rules()
        assert listed[0]["subject"] == "limits.%s@example.com" % suffix
    finally:
        db.session.delete(user)
        db.session.commit()


def test_entries_that_cannot_be_saved_say_why(clean):
    with pytest.raises(clean.RuleError, match="203.0.113.0/24"):
        clean.add_rule("deny", "address", "not-an-address")
    with pytest.raises(clean.RuleError, match="No user has that email"):
        clean.add_rule("deny", "user", "nobody-%s@example.com" % uuid.uuid4().hex)
    clean.add_rule("deny", "address", "192.0.2.9")
    with pytest.raises(clean.RuleError, match="already on the list"):
        clean.add_rule("deny", "address", "192.0.2.9")
    with pytest.raises(clean.RuleError, match="lose access"):
        clean.add_rule("deny", "address", "192.0.2.0/24", own_address="192.0.2.77")
    # Allowing your own address is harmless and permitted.
    clean.add_rule("allow", "address", "192.0.2.0/24", own_address="192.0.2.77")


def test_a_change_is_seen_by_the_next_read_on_any_worker(clean):
    """The cache is keyed by the list's version: an entry added by someone else shows up on the next read."""
    from app import db

    assert clean.current_rules() == ()
    with db.engine.begin() as connection:  # another worker's write
        connection.execute(text(
            "INSERT INTO rate_limit_rules (action, kind, value, created_at) "
            "VALUES ('deny', 'address', '192.0.2.50', now())"))
    assert [r[4] for r in clean.current_rules()] == ["192.0.2.50"]
    with db.engine.begin() as connection:
        connection.execute(text("DELETE FROM rate_limit_rules"))
    assert clean.current_rules() == ()


# ---- the violation log -------------------------------------------------------------------------

def _violation(org_id, address, at=None, rule="denied: x"):
    from app import db

    with db.engine.begin() as connection:
        connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
        connection.execute(
            text("INSERT INTO rate_limit_violations (organization_id, address, route, rule, created_at) "
                 "VALUES (:o, :a, 'GET /x', :r, :t)"),
            {"o": org_id, "a": address, "r": rule, "t": at or datetime.utcnow()})


@pytest.fixture
def two_orgs(app, clean):
    from app import db
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    orgs = [Organization(name="Limits %s %s" % (n, suffix), slug="limits-%s-%s" % (n, suffix)) for n in "ab"]
    db.session.add_all(orgs)
    db.session.commit()
    ids = [o.id for o in orgs]
    yield ids
    with db.engine.begin() as connection:
        connection.execute(text("DELETE FROM rate_limit_violations"))
    db.session.expire_all()
    for org in orgs:
        db.session.delete(db.session.get(Organization, org.id))
    db.session.commit()


def test_an_organisation_reads_only_its_own_refusals(two_orgs, clean):
    org_a, org_b = two_orgs
    _violation(org_a, "198.51.100.1")
    _violation(org_b, "198.51.100.2")
    _violation(None, "198.51.100.3")
    assert {r["address"] for r in clean.violation_page(organization_id=org_a)["rows"]} == {"198.51.100.1"}
    assert {r["address"] for r in clean.violation_page(organization_id=org_b)["rows"]} == {"198.51.100.2"}
    every = {r["address"] for r in clean.violation_page()["rows"]}
    assert every == {"198.51.100.1", "198.51.100.2", "198.51.100.3"}


def test_the_log_filters_by_address_and_date_and_pages(two_orgs, clean):
    org_a, _ = two_orgs
    now = datetime.utcnow()
    for index in range(30):
        _violation(org_a, "198.51.100.7", at=now - timedelta(minutes=index))
    _violation(org_a, "203.0.113.9", at=now - timedelta(days=3))
    page = clean.violation_page(organization_id=org_a, per_page=25)
    assert page["total"] == 31 and page["pages"] == 2 and len(page["rows"]) == 25
    assert clean.violation_page(organization_id=org_a, address="203.0.113")["total"] == 1
    assert clean.violation_page(organization_id=org_a, since=now - timedelta(days=1))["total"] == 30
    assert clean.violation_page(organization_id=org_a, page=2, per_page=25)["rows"][-1]["address"] == "203.0.113.9"


def test_refusals_older_than_thirty_days_are_gone(two_orgs, clean):
    org_a, _ = two_orgs
    _violation(org_a, "198.51.100.8", at=datetime.utcnow() - timedelta(days=31))
    _violation(org_a, "198.51.100.9", at=datetime.utcnow() - timedelta(days=29))
    assert {r["address"] for r in clean.violation_page(organization_id=org_a)["rows"]} == {"198.51.100.9"}
    assert clean.purge_violations() == 1
    assert clean.violation_page(organization_id=org_a)["total"] == 1


# ---- the request path --------------------------------------------------------------------------

@pytest.fixture
def limiting_on(app):
    was = app.config.get("RATE_LIMITING_ENABLED")
    app.config["RATE_LIMITING_ENABLED"] = True
    yield
    app.config["RATE_LIMITING_ENABLED"] = was


def test_a_denied_address_is_refused_in_a_sentence_and_logged(app, clean, limiting_on):
    clean.add_rule("deny", "address", "192.0.2.10")
    client = app.test_client()
    environ = {"REMOTE_ADDR": "192.0.2.10"}
    html = client.get("/account/login", environ_overrides=environ)
    assert html.status_code == 403 and clean.DENIED_MESSAGE in html.get_data(as_text=True)
    api = client.get("/api/v1/capabilities/", environ_overrides=environ, headers={"Accept": "application/json"})
    assert api.status_code == 403
    assert api.get_json() == {"success": False, "error": clean.DENIED_MESSAGE, "error_type": "address_blocked"}
    logged = clean.violation_page()["rows"]
    assert {(r["address"], r["rule"]) for r in logged} == {("192.0.2.10", "denied: 192.0.2.10")}
    # Someone else is not affected, and a forwarded-for header from the caller does not change the address.
    assert client.get("/account/login", environ_overrides={"REMOTE_ADDR": "192.0.2.11"}).status_code == 200


def test_every_limited_response_reports_the_allowance(app, clean, limiting_on):
    client = app.test_client()
    environ = {"REMOTE_ADDR": "192.0.2.%d" % (20 + uuid.uuid4().int % 200)}
    first = client.get("/account/login", environ_overrides=environ)
    second = client.get("/account/login", environ_overrides=environ)
    for response in (first, second):
        for name in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
            assert response.headers.get(name), (name, dict(response.headers))
    assert int(second.headers["RateLimit-Remaining"]) == int(first.headers["RateLimit-Remaining"]) - 1


def test_an_allowed_address_is_not_counted_and_deny_still_wins(app, clean, limiting_on):
    clean.add_rule("allow", "address", "192.0.2.0/24")
    client = app.test_client()
    environ = {"REMOTE_ADDR": "192.0.2.30"}
    response = client.get("/account/login", environ_overrides=environ)
    assert response.status_code == 200 and "RateLimit-Remaining" not in response.headers
    clean.add_rule("deny", "address", "192.0.2.30")
    assert client.get("/account/login", environ_overrides=environ).status_code == 403


def test_the_limit_refuses_with_retry_after_and_logs_it(app, clean, limiting_on):
    client = app.test_client()
    environ = {"REMOTE_ADDR": "192.0.2.99"}
    codes = [client.get("/account/login", environ_overrides=environ).status_code for _ in range(125)]
    assert 429 in codes
    refused = client.get("/account/login", environ_overrides=environ)
    assert refused.status_code == 429 and refused.headers.get("Retry-After")
    assert refused.headers.get("RateLimit-Remaining") == "0"
    rows = clean.violation_page(address="192.0.2.99")["rows"]
    assert rows and rows[0]["route"] == "GET /account/login" and "minute" in rows[0]["rule"]


def test_the_pages_are_for_administrators_and_a_plain_member_is_refused(app, clean):
    client = app.test_client()
    assert client.get("/admin/rate-limits").status_code in (302, 401, 403)
    assert client.post("/admin/rate-limits/rules", data={"action": "deny", "value": "192.0.2.1"}).status_code in (302, 401, 403)
    assert clean.list_rules() == []


@pytest.fixture
def clean_between_requests(app):
    """Like ``clean`` but holds no application context open, so each request gets its own ``g``."""
    from app import db
    from app.services import rate_limiter

    def wipe():
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(text("DELETE FROM rate_limit_rules"))
                connection.execute(text("DELETE FROM rate_limit_counters"))
                connection.execute(text("DELETE FROM rate_limit_violations"))
            rate_limiter._rules_cache["version"] = None

    wipe()
    yield rate_limiter
    wipe()


def _nonces(response):
    import re

    header = set(re.findall(r"'nonce-([^']*)'", response.headers.get("Content-Security-Policy", "")))
    body = set(re.findall(r'nonce="([^"]*)"', response.get_data(as_text=True)))
    return header, body


def test_the_default_limit_refusal_carries_the_nonce_the_header_names(app, clean_between_requests, limiting_on):
    """The 121st read in a minute is refused by the limiter's own hook, before any page hook has run."""
    client = app.test_client()
    environ = {"REMOTE_ADDR": "192.0.2.77"}
    refused = None
    for _ in range(125):
        refused = client.get("/account/login", environ_overrides=environ)
        if refused.status_code == 429:
            break
    assert refused.status_code == 429
    header, body = _nonces(refused)
    assert len(header) == 1 and "" not in header, header
    assert body == header, (header, body)
    # The general allowance holds every page, so the page offers no sign-in or dashboard link.
    html = refused.get_data(as_text=True)
    assert "Sign in" not in html and "Go to Dashboard" not in html and 'href="' not in html.split("<main")[1]


def test_the_blocked_address_page_carries_the_nonce_and_offers_only_what_it_can_reach(app, clean_between_requests, limiting_on):
    with app.app_context():
        clean_between_requests.add_rule("deny", "address", "192.0.2.78")
    refused = app.test_client().get("/account/login", environ_overrides={"REMOTE_ADDR": "192.0.2.78"})
    assert refused.status_code == 403
    header, body = _nonces(refused)
    assert len(header) == 1 and "" not in header and body == header, (header, body)
    html = refused.get_data(as_text=True)
    assert "Go to Dashboard" not in html and 'href="/"' not in html


def test_a_role_refusal_still_offers_the_dashboard(app):
    with app.test_request_context("/"):
        from flask import render_template

        assert "Go to Dashboard" in render_template("errors/403.html")


def test_one_function_issues_the_nonce_whatever_order_the_hooks_ran(app):
    from app._bootstrap.security import ensure_csp_nonce

    with app.test_request_context("/"):
        first = ensure_csp_nonce()
        assert first and ensure_csp_nonce() == first


def test_an_unreadable_counter_store_lets_the_request_through_and_says_so(app, clean, limiting_on, caplog):
    import logging

    class Broken:
        def incr(self, *args, **kwargs):
            raise RuntimeError("counter store down")

    with app.test_request_context("/account/register"):
        original = clean.counter_storage
        clean.counter_storage = lambda: Broken()
        try:
            with caplog.at_level(logging.WARNING):
                verdict = clean.count_hit("signup", "192.0.2.79", 5, 3600)
        finally:
            clean.counter_storage = original
    assert verdict.allowed is True
    assert "rate limit store unavailable" in caplog.text and "counter store down" in caplog.text


def test_unreadable_allow_and_deny_lists_do_not_take_the_page_down(app, clean, limiting_on, monkeypatch, caplog):
    import logging

    def broken(*args, **kwargs):
        raise RuntimeError("lists down")

    monkeypatch.setattr(clean, "access_verdict", broken)
    with caplog.at_level(logging.WARNING):
        response = app.test_client().get("/account/login", environ_overrides={"REMOTE_ADDR": "192.0.2.80"})
    assert response.status_code == 200 and "lists down" in caplog.text
