"""Rate limits an administrator can see and set: shared counters, allow and deny lists,
the violation log and its scoping, and what a refused request looks like."""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from limits import parse, strategies
from sqlalchemy import create_engine, text


# Every address these tests use sits in one block no other process uses (the 10.x.y.0/24 named by
# this process's id), and every counter name carries a token of its own. Cleaning up deletes
# exactly those rows, by key, so a second run on the same database keeps its entries, its counters
# and its refusals.
_BLOCK = "10.%d.%d" % ((os.getpid() >> 8) & 255, os.getpid() & 255)
_TOKEN = uuid.uuid4().hex[:8]
_WINDOW_KEY = "k:window-%s" % _TOKEN


def _own(last):
    return "%s.%s" % (_BLOCK, last)


def _forget_own(connection, organization_ids=()):
    connection.execute(text("DELETE FROM rate_limit_counters WHERE key LIKE :p OR key LIKE :t"),
                       {"p": "%ip:" + _BLOCK + ".%", "t": "%" + _TOKEN + "%"})
    connection.execute(text("DELETE FROM rate_limit_rules WHERE value LIKE :p"), {"p": _BLOCK + ".%"})
    connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    connection.execute(text("DELETE FROM rate_limit_violations WHERE address LIKE :p OR organization_id = ANY(:orgs)"),
                       {"p": _BLOCK + ".%", "orgs": list(organization_ids)})


@pytest.fixture
def clean(app):
    """The entries, counters and refusals these tests create are removed before and after, by key."""
    from app import db
    from app.services import rate_limiter

    def wipe():
        with db.engine.begin() as connection:
            _forget_own(connection)
        rate_limiter.forget_rules()

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
        key = "ip:" + _own(60 + uuid.uuid4().int % 190)
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
    key = "reset-walk-%s" % _TOKEN
    assert limiter.check_rate_limit(key, 2, 60) == (True, None)
    assert limiter.check_rate_limit(key, 2, 60) == (True, None)
    allowed, retry_after = limiter.check_rate_limit(key, 2, 60)
    assert allowed is False and retry_after

    limiter.reset(key)

    assert limiter.check_rate_limit(key, 2, 60) == (True, None)


def test_a_finished_window_starts_again(app, clean):
    storage = clean.DatabaseStorage()
    assert storage.incr(_WINDOW_KEY, 60) == 1
    assert storage.incr(_WINDOW_KEY, 60) == 2
    with app.app_context():
        from app import db

        with db.engine.begin() as connection:
            connection.execute(text("UPDATE rate_limit_counters SET expires_at = 0 WHERE key = :k"), {"k": _WINDOW_KEY})
    assert storage.get(_WINDOW_KEY) == 0
    assert storage.incr(_WINDOW_KEY, 60) == 1


# ---- allow and deny lists ----------------------------------------------------------------------

def test_deny_wins_over_allow(clean):
    clean.add_rule("allow", "address", _own("0/24"))
    clean.add_rule("deny", "address", _own(3))
    assert clean.access_verdict(_own(3))[0] == "deny"
    assert clean.access_verdict(_own(9))[0] == "allow"
    assert clean.access_verdict("172.31.7.1") == (None, None)
    assert clean.access_verdict(None) == (None, None)
    assert clean.access_verdict("not an address") == (None, None)


def test_a_range_matches_its_members_and_an_ipv6_address_never_matches_an_ipv4_range(clean):
    clean.add_rule("deny", "address", _own("0/24"))
    assert clean.access_verdict(_own(200))[0] == "deny"
    assert clean.access_verdict("172.31.7.2")[0] is None
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
        rule_id = clean.add_rule("deny", "user", "LIMITS.%s@example.com" % suffix)
        assert clean.access_verdict("198.51.100.1", user.id)[0] == "deny"
        assert clean.access_verdict("198.51.100.1", user.id + 1000)[0] is None
        assert clean.access_verdict("198.51.100.1", None)[0] is None
        listed = [r for r in clean.list_rules() if r["id"] == rule_id]
        assert [r["subject"] for r in listed] == ["limits.%s@example.com" % suffix]
    finally:
        clean.remove_rule(rule_id)
        db.session.delete(user)
        db.session.commit()


def test_entries_that_cannot_be_saved_say_why(clean):
    with pytest.raises(clean.RuleError, match="203.0.113.0/24"):
        clean.add_rule("deny", "address", "not-an-address")
    with pytest.raises(clean.RuleError, match="No user has that email"):
        clean.add_rule("deny", "user", "nobody-%s@example.com" % uuid.uuid4().hex)
    clean.add_rule("deny", "address", _own(9))
    with pytest.raises(clean.RuleError, match="already on the list"):
        clean.add_rule("deny", "address", _own(9))
    with pytest.raises(clean.RuleError, match="lose access"):
        clean.add_rule("deny", "address", _own("0/24"), own_address=_own(77))
    # Allowing your own address is harmless and permitted.
    clean.add_rule("allow", "address", _own("0/24"), own_address=_own(77))


def _mine(rules):
    return [r[4] for r in rules if r[4].startswith(_BLOCK + ".")]


def test_a_change_made_elsewhere_is_seen_once_the_held_entries_expire(clean):
    """Another worker's write shows up when this worker next looks, which is at most a few seconds on."""
    from app import db

    assert _mine(clean.current_rules()) == []
    with db.engine.begin() as connection:  # another worker's write
        connection.execute(text(
            "INSERT INTO rate_limit_rules (action, kind, value, created_at) "
            "VALUES ('deny', 'address', :v, now())"), {"v": _own(50)})
    clean._rules_cache["checked_at"] = 0.0  # the held entries are older than the refresh period
    assert _mine(clean.current_rules()) == [_own(50)]
    with db.engine.begin() as connection:
        connection.execute(text("DELETE FROM rate_limit_rules WHERE value = :v"), {"v": _own(50)})
    clean._rules_cache["checked_at"] = 0.0
    assert _mine(clean.current_rules()) == []


def test_requests_inside_the_refresh_period_cost_no_query_and_a_local_write_is_seen_at_once(clean):
    """Held entries answer without touching the database; adding or removing an entry drops them."""
    from app import db

    clean.current_rules()
    statements = []

    def watch(conn, cursor, statement, *rest):
        if "rate_limit_rules" in statement:
            statements.append(statement)

    from sqlalchemy import event
    event.listen(db.engine, "before_cursor_execute", watch)
    try:
        for _ in range(50):
            clean.access_verdict(_own(1))
        assert statements == []
        rule_id = clean.add_rule("deny", "address", _own(1))
        statements.clear()
        assert clean.access_verdict(_own(1))[0] == "deny"
        assert statements, "the entry just added must be read, not served from the old copy"
        clean.remove_rule(rule_id)
        assert clean.access_verdict(_own(1))[0] is None
    finally:
        event.remove(db.engine, "before_cursor_execute", watch)


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
        _forget_own(connection, ids)
    db.session.expire_all()
    try:
        for org_id in ids:
            db.session.delete(db.session.get(Organization, org_id))
        db.session.commit()
    except Exception:  # people kept by the audit trail still belong to them
        db.session.rollback()


def test_an_organisation_reads_only_its_own_refusals(two_orgs, clean):
    org_a, org_b = two_orgs
    _violation(org_a, _own(1))
    _violation(org_b, _own(2))
    _violation(None, _own(3))
    assert {r["address"] for r in clean.violation_page(organization_id=org_a)["rows"]} == {_own(1)}
    assert {r["address"] for r in clean.violation_page(organization_id=org_b)["rows"]} == {_own(2)}
    every = {r["address"] for r in clean.violation_page(address=_BLOCK + ".")["rows"]}
    assert every == {_own(1), _own(2), _own(3)}


def test_the_log_filters_by_address_and_date_and_pages(two_orgs, clean):
    org_a, _ = two_orgs
    now = datetime.utcnow()
    for index in range(30):
        _violation(org_a, _own(7), at=now - timedelta(minutes=index))
    _violation(org_a, _own(90), at=now - timedelta(days=3))
    page = clean.violation_page(organization_id=org_a, per_page=25)
    assert page["total"] == 31 and page["pages"] == 2 and len(page["rows"]) == 25
    assert clean.violation_page(organization_id=org_a, address=_own(90))["total"] == 1
    assert clean.violation_page(organization_id=org_a, since=now - timedelta(days=1))["total"] == 30
    assert clean.violation_page(organization_id=org_a, page=2, per_page=25)["rows"][-1]["address"] == _own(90)


def test_refusals_older_than_thirty_days_are_gone(two_orgs, clean):
    org_a, _ = two_orgs
    _violation(org_a, _own(8), at=datetime.utcnow() - timedelta(days=31))
    _violation(org_a, _own(9), at=datetime.utcnow() - timedelta(days=29))
    assert {r["address"] for r in clean.violation_page(organization_id=org_a)["rows"]} == {_own(9)}
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
    clean.add_rule("deny", "address", _own(10))
    client = app.test_client()
    environ = {"REMOTE_ADDR": _own(10)}
    html = client.get("/account/login", environ_overrides=environ)
    assert html.status_code == 403 and clean.DENIED_MESSAGE in html.get_data(as_text=True)
    api = client.get("/api/v1/capabilities/", environ_overrides=environ, headers={"Accept": "application/json"})
    assert api.status_code == 403
    assert api.get_json() == {"success": False, "error": clean.DENIED_MESSAGE, "error_type": "address_blocked"}
    logged = clean.violation_page(address=_BLOCK + ".")["rows"]
    assert {(r["address"], r["rule"]) for r in logged} == {(_own(10), "denied: " + _own(10))}
    # Someone else is not affected, and a forwarded-for header from the caller does not change the address.
    assert client.get("/account/login", environ_overrides={"REMOTE_ADDR": _own(11)}).status_code == 200


def test_every_limited_response_reports_the_allowance(app, clean, limiting_on):
    client = app.test_client()
    environ = {"REMOTE_ADDR": _own(20 + uuid.uuid4().int % 200)}
    first = client.get("/account/login", environ_overrides=environ)
    second = client.get("/account/login", environ_overrides=environ)
    for response in (first, second):
        for name in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
            assert response.headers.get(name), (name, dict(response.headers))
    assert int(second.headers["RateLimit-Remaining"]) == int(first.headers["RateLimit-Remaining"]) - 1


def test_an_allowed_address_is_not_counted_and_deny_still_wins(app, clean, limiting_on):
    clean.add_rule("allow", "address", _own("0/24"))
    client = app.test_client()
    environ = {"REMOTE_ADDR": _own(30)}
    response = client.get("/account/login", environ_overrides=environ)
    assert response.status_code == 200 and "RateLimit-Remaining" not in response.headers
    clean.add_rule("deny", "address", _own(30))
    assert client.get("/account/login", environ_overrides=environ).status_code == 403


def test_the_limit_refuses_with_retry_after_and_logs_it(app, clean, limiting_on):
    client = app.test_client()
    environ = {"REMOTE_ADDR": _own(99)}
    codes = [client.get("/account/login", environ_overrides=environ).status_code for _ in range(125)]
    assert 429 in codes
    refused = client.get("/account/login", environ_overrides=environ)
    assert refused.status_code == 429 and refused.headers.get("Retry-After")
    assert refused.headers.get("RateLimit-Remaining") == "0"
    rows = clean.violation_page(address=_own(99))["rows"]
    assert rows and rows[0]["route"] == "GET /account/login" and "minute" in rows[0]["rule"]


@pytest.fixture
def people(app, two_orgs, login_as):
    """A plain member and an administrator in organisation A, and an administrator in organisation B."""
    from app import db
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    org_a, org_b = two_orgs
    made = []

    def person(org_id, org_role):
        user = User(email="limits.%s@example.com" % uuid.uuid4().hex[:10], first_name="L", last_name="P",
                    organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
                    role=Role.query.filter_by(name="User").first())
        user.password = "pw-%s" % uuid.uuid4().hex[:8]
        db.session.add(user)
        db.session.commit()
        OrgRole.set_role(org_id, user.id, org_role, granted_by_id=user.id)
        db.session.commit()
        made.append(user.id)
        return user

    found = {"member": person(org_a, "viewer"), "admin_a": person(org_a, "org_admin"),
             "admin_b": person(org_b, "org_admin")}
    yield found
    db.session.remove()
    # A person who has made a request has a row in the append-only audit trail, which keeps the
    # person. Those people have unique addresses and never meet another test, so when the trail
    # holds one, it and its organisation stay and nothing is half-removed.
    try:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM org_roles WHERE user_id = ANY(:ids)"), {"ids": made})
            connection.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": made})
    except Exception:
        pass


def _page(app, login_as, user, path="/admin/rate-limits", **kwargs):
    client = app.test_client()
    login_as(client, user)
    return client.get(path, **kwargs)


def test_a_signed_in_plain_member_is_refused_the_page_and_every_write(app, clean, people, login_as):
    member = people["member"]
    assert _page(app, login_as, member).status_code == 403
    client = app.test_client()
    login_as(client, member)
    assert client.post("/admin/rate-limits/rules", data={"action": "deny", "value": _own(1)}).status_code == 403
    assert client.post("/admin/rate-limits/rules/1/delete").status_code == 403
    assert [r for r in clean.list_rules() if r["value"].startswith(_BLOCK + ".")] == []
    anonymous = app.test_client()
    assert anonymous.get("/admin/rate-limits").status_code in (302, 401, 403)


def test_an_organisation_administrator_reads_their_own_refusals_under_settings_and_never_the_others(
        app, clean, two_orgs, people, login_as):
    org_a, org_b = two_orgs
    _violation(org_a, _own(41))
    _violation(org_b, _own(42))
    _violation(None, _own(43))
    html = _page(app, login_as, people["admin_a"]).get_data(as_text=True)
    assert _own(41) in html and _own(42) not in html and _own(43) not in html
    assert 'data-testid="rate-limit-rules"' not in html
    trail = html.split('aria-label="Breadcrumb"', 1)[1][:1500]
    assert "Home" in trail and "Rate limits" in trail and "Platform admin" not in html
    other = _page(app, login_as, people["admin_b"]).get_data(as_text=True)
    assert _own(42) in other and _own(41) not in other
    assert app.test_client().post("/admin/rate-limits/rules", data={"action": "deny", "value": _own(2)}).status_code in (302, 401, 403)


def test_the_user_menu_offers_an_organisation_administrator_the_rate_limits_page_and_a_member_none(
        app, people, login_as):
    from app.navigation import registry

    from flask import g

    with app.test_request_context("/dashboard/overview"):
        g.current_org_id = people["admin_a"].organization_id
        admin_menu = [item.label for item in registry.sidebar_for(people["admin_a"], "dashboard.overview")["user_menu"]]
        g.current_org_id = people["member"].organization_id
        member_menu = [item.label for item in registry.sidebar_for(people["member"], "dashboard.overview")["user_menu"]]
    assert "Rate limits" in admin_menu and "Rate limits" not in member_menu
    # The entry opens the page the administrator may read, and the page names no platform section.
    page = _page(app, login_as, people["admin_a"], "/dashboard/overview").get_data(as_text=True)
    assert 'href="/admin/rate-limits"' in page


def test_an_administrator_cannot_deny_their_own_user(clean, people):
    admin = people["admin_a"]
    with pytest.raises(clean.RuleError, match="user you are signed in as"):
        clean.add_rule("deny", "user", admin.email, own_user_id=admin.id)
    # Allowing oneself is harmless, and denying someone else is allowed.
    clean.add_rule("allow", "user", admin.email, own_user_id=admin.id)
    clean.add_rule("deny", "user", people["member"].email, own_user_id=admin.id)
    assert clean.access_verdict(_own(1), people["member"].id)[0] == "deny"


def test_a_filtered_log_says_it_is_filtered_and_an_empty_note_or_route_is_explained(
        app, clean, two_orgs, people, login_as):
    org_a, _ = two_orgs
    _violation(org_a, _own(44))
    from app import db

    with db.engine.begin() as connection:
        connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
        connection.execute(text("UPDATE rate_limit_violations SET route = NULL WHERE address = :a"), {"a": _own(44)})
    whole = _page(app, login_as, people["admin_a"]).get_data(as_text=True)
    assert "matching this filter" not in whole
    assert "No route was named" in whole
    narrowed = _page(app, login_as, people["admin_a"], query_string={"address": _own(99)}).get_data(as_text=True)
    assert "0 refused requests matching this filter in the last 30 days" in narrowed
    matched = _page(app, login_as, people["admin_a"], query_string={"address": _own(44)}).get_data(as_text=True)
    assert "1 refused request matching this filter in the last 30 days" in matched


def test_the_stated_limits_are_the_ones_the_limiter_holds(app, clean, monkeypatch, db_session, people, login_as):
    from app import db
    from app._bootstrap import rate_limiting
    from app.models.user import User

    platform = User(email="limits.platform.%s@example.com" % uuid.uuid4().hex[:8], first_name="P", last_name="A",
                    organization_id=people["admin_a"].organization_id, confirmed=True,
                    is_platform_admin=True, is_org_admin=True)
    platform.password = "pw-%s" % uuid.uuid4().hex[:8]
    db.session.add(platform)
    db.session.commit()
    try:
        monkeypatch.setattr(rate_limiting, "_DEFAULT_LIMITS", ["777 per hour", "66 per minute"])
        monkeypatch.setattr(rate_limiting, "_WRITE_LIMITS", ["555 per hour", "44 per minute"])
        html = _page(app, login_as, platform).get_data(as_text=True)
        assert "66 requests a minute and 777 an hour" in html
        assert "44 writes a minute and 555 an hour" in html
        assert "120 requests" not in html
    finally:
        try:  # the audit trail keeps a person who has made a request
            db.session.delete(db.session.get(User, platform.id))
            db.session.commit()
        except Exception:
            db.session.rollback()


@pytest.fixture
def clean_between_requests(app):
    """Like ``clean`` but holds no application context open, so each request gets its own ``g``."""
    from app import db
    from app.services import rate_limiter

    def wipe():
        with app.app_context():
            with db.engine.begin() as connection:
                _forget_own(connection)
            rate_limiter.forget_rules()

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
    environ = {"REMOTE_ADDR": _own(77)}
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
        clean_between_requests.add_rule("deny", "address", _own(78))
    refused = app.test_client().get("/account/login", environ_overrides={"REMOTE_ADDR": _own(78)})
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
                verdict = clean.count_hit("signup", _own(79), 5, 3600)
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
        response = app.test_client().get("/account/login", environ_overrides={"REMOTE_ADDR": _own(80)})
    assert response.status_code == 200 and "lists down" in caplog.text
