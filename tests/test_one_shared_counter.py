"""Every limiter counts in the one shared store, so two workers share one count.

The request limiter, the route decorator, the import limiter, the ADM limiter, the sidebar and
composer limits and the sign-up limits all count through ``count_hit`` into the same table. These
tests run each limiter on two simulated worker pools (two engines, as two gunicorn workers have) and
check they share one count, then drive the real routes as signed-in people of two organisations.
"""

import inspect
import uuid

import pytest
from sqlalchemy import create_engine, text


# Counters these tests create are found by the identity each one counts against: the documentation
# addresses it uses, the name its decorated route counts under, and the ids it makes up. Nothing
# else in the table is touched, so another run on the same database keeps its own counts.
_OWN_COUNTER_FRAGMENTS = ["198.51.100.", "shared:route-test"]


def _forget_own_counters(connection):
    for fragment in _OWN_COUNTER_FRAGMENTS:
        connection.execute(text("DELETE FROM rate_limit_counters WHERE key LIKE :p"), {"p": "%" + fragment + "%"})


@pytest.fixture
def counters(app):
    """Throttling on, an empty counter table, and two storages on separate connection pools."""
    from app import db
    from app.services import rate_limiter

    was = app.config.get("RATE_LIMITING_ENABLED")
    app.config["RATE_LIMITING_ENABLED"] = True

    def wipe():
        with db.engine.begin() as connection:
            _forget_own_counters(connection)
            connection.execute(text("DELETE FROM rate_limit_violations"))

    # No application context is held open for the test: one would cache the signed-in user between
    # requests and key every sign-up by a different person.
    engines = []
    with app.app_context():
        wipe()
        pair = []
        for _ in range(2):
            engine = create_engine(db.engine.url)
            engines.append(engine)
            pair.append(rate_limiter.DatabaseStorage(engine=engine))
    yield rate_limiter, pair
    rate_limiter._storage_override["storage"] = None
    with app.app_context():
        wipe()
    app.config["RATE_LIMITING_ENABLED"] = was
    for engine in engines:
        engine.dispose()


def _alternate(rate_limiter, pair, turns, action):
    """Run ``action`` ``turns`` times, each turn on the other worker's pool; collect the outcomes."""
    outcomes = []
    for turn in range(turns):
        rate_limiter._storage_override["storage"] = pair[turn % 2]
        outcomes.append(action())
    return outcomes


def _refused(call):
    def action():
        try:
            call()
        except Exception as error:  # the refusal each limiter raises or returns is checked below
            return type(error).__name__
        return "allowed"

    return action


def test_the_route_decorator_shares_one_count_between_workers(app, counters):
    rate_limiter, pair = counters

    @rate_limiter.rate_limit(4, "1m", key_func=lambda: "shared:" + "route-test")
    def view():
        return "ok"

    with app.test_request_context("/x"):
        outcomes = _alternate(rate_limiter, pair, 7, _refused(view))
    assert outcomes == ["allowed"] * 4 + ["RateLimitExceeded"] * 3, outcomes


def test_the_import_limiter_shares_one_count_between_workers(app, counters):
    from app.utils.import_rate_limiter import check_import_limit

    rate_limiter, pair = counters
    with app.test_request_context("/import", environ_base={"REMOTE_ADDR": "198.51.100.7"}):
        outcomes = _alternate(
            rate_limiter, pair, 5, lambda: check_import_limit(3, 100, 500)[0].allowed
        )
    assert outcomes == [True, True, True, False, False], outcomes


def test_the_import_limiter_honours_the_allowance_each_route_declares(app, counters):
    from app.utils.import_rate_limiter import check_import_limit

    rate_limiter, pair = counters
    with app.test_request_context("/import", environ_base={"REMOTE_ADDR": "198.51.100.8"}):
        rate_limiter._storage_override["storage"] = pair[0]
        verdicts = [check_import_limit(2, 100, 500)[0].allowed for _ in range(3)]
    assert verdicts == [True, True, False]


def test_the_adm_limiter_shares_one_count_between_workers(app, counters):
    from app.utils.adm_rate_limiter import ADMRateLimitExceeded, adm_rate_limiter

    rate_limiter, pair = counters
    user_id = 900000 + uuid.uuid4().int % 90000
    _OWN_COUNTER_FRAGMENTS.append(":%d" % user_id)

    def action():
        try:
            return adm_rate_limiter.check_rate_limit(user_id, "transition")["remaining"]
        except ADMRateLimitExceeded as refusal:
            return "refused after %s" % refusal.verdict.limit

    with app.test_request_context("/adm"):
        outcomes = _alternate(rate_limiter, pair, 7, action)
    assert outcomes == [4, 3, 2, 1, 0, "refused after 5", "refused after 5"], outcomes


def test_the_sidebar_and_composer_limits_share_one_count_between_workers(app, counters):
    from flask import jsonify

    from app.routes.sidebar_api_hardened import rate_limit as sidebar_limit
    from app.utils.composer_rate_limiter import composer_rate_limit

    rate_limiter, pair = counters

    @sidebar_limit(calls_per_minute=3)
    def sidebar():
        return jsonify({"ok": True})

    @composer_rate_limit
    def composer():
        return jsonify({"ok": True})

    def status(view):
        def action():
            response = view()
            return (response[0] if isinstance(response, tuple) else response).status_code

        return action

    with app.test_request_context("/api/sidebar/x", environ_base={"REMOTE_ADDR": "198.51.100.9"}):
        assert _alternate(rate_limiter, pair, 5, status(sidebar)) == [200, 200, 200, 429, 429]
    with app.test_request_context("/composer", environ_base={"REMOTE_ADDR": "198.51.100.10"}):
        assert _alternate(rate_limiter, pair, 12, status(composer)) == [200] * 10 + [429, 429]


def test_no_limiter_keeps_a_count_in_process_memory():
    """The in-memory stores are gone: a count kept in one process is a different limit per worker."""
    from app.routes import sidebar_api_hardened, webhook
    from app.services import rate_limiter
    from app.utils import adm_rate_limiter, composer_rate_limiter, import_rate_limiter

    for module in (adm_rate_limiter, composer_rate_limiter, import_rate_limiter, rate_limiter,
                   sidebar_api_hardened, webhook):
        source = inspect.getsource(module)
        for in_memory in ("defaultdict", "deque", "_buckets", "self._requests", "_counters = ", "_counters: dict"):
            assert in_memory not in source, "%s still keeps a count in memory (%s)" % (module.__name__, in_memory)
    assert not hasattr(rate_limiter.RateLimiter(), "_buckets")


# ---- the real routes, as signed-in people of two organisations ---------------------------------

@pytest.fixture
def two_members(db_session, make_org):
    from app.models.user import User

    members = []
    for label in ("alpha", "beta"):
        org = make_org("burst-" + label)
        user = User(email="burst-%s-%s@example.test" % (label, uuid.uuid4().hex[:8]),
                    organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
        db_session.add(user)
        db_session.flush()
        members.append(user)
    return members


def _headers_say_what_is_left(response):
    for name in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
        assert response.headers.get(name), (name, dict(response.headers))
        assert len(response.headers.getlist(name)) == 1, (name, response.headers.getlist(name))


def test_an_adm_burst_is_refused_plainly_with_the_allowance_in_headers(app, counters, two_members, login_as):
    member_a, member_b = two_members
    client_a, client_b = app.test_client(), app.test_client()
    path = "/api/adm-kanban/cards/1/transition-request"
    seen = []
    for _ in range(7):
        login_as(client_a, member_a)
        seen.append(client_a.post(path, json={}))
    codes = [response.status_code for response in seen]
    assert 429 not in codes[:5] and codes[5:] == [429, 429], codes
    _headers_say_what_is_left(seen[0])
    refused = seen[5]
    _headers_say_what_is_left(refused)
    assert refused.headers["RateLimit-Remaining"] == "0" and int(refused.headers["Retry-After"]) > 0
    body = refused.get_json()
    assert body["success"] is False and "Try again in" in body["error"] and body["retry_after"] > 0
    # The other organisation's member has not used any of it.
    login_as(client_b, member_b)
    assert client_b.post(path, json={}).status_code != 429


def test_an_import_burst_is_refused_plainly_with_the_allowance_in_headers(app, counters, two_members, login_as):
    member_a, member_b = two_members
    client_a, client_b = app.test_client(), app.test_client()
    rule = next(r for r in app.url_map.iter_rules() if r.rule.endswith("/analyze-import-stream") and "POST" in r.methods)
    seen = []
    for _ in range(7):
        login_as(client_a, member_a)
        seen.append(client_a.post(rule.rule, data={}))
    codes = [response.status_code for response in seen]
    assert 429 not in codes[:5] and codes[5:] == [429, 429], codes
    refused = seen[5]
    _headers_say_what_is_left(refused)
    assert refused.headers["RateLimit-Remaining"] == "0" and int(refused.headers["Retry-After"]) > 0
    body = refused.get_json()
    assert body["success"] is False and "Too many imports" in body["error"] and "Try again in" in body["error"]
    login_as(client_b, member_b)
    assert client_b.post(rule.rule, data={}).status_code != 429


# ---- sign-up ------------------------------------------------------------------------------------

@pytest.fixture
def signed_up_cleanup(app):
    """Remove the people the sign-up tests create, where the audit trail allows it.

    A new account is recorded in the append-only audit log, which keeps its rows and so keeps the
    account they name; those accounts have unique addresses and never meet another test.
    """
    from app import db

    yield
    with app.app_context():
        try:
            with db.engine.begin() as connection:
                connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
                connection.execute(text(
                    "DELETE FROM org_roles WHERE user_id IN "
                    "(SELECT id FROM users WHERE email LIKE 'signup-limit-%@example.com')"))
                connection.execute(text("DELETE FROM users WHERE email LIKE 'signup-limit-%@example.com'"))
        except Exception:  # audit rows point at them: nothing is removed, nothing is half-removed
            pass


def _signup_form(email):
    return {"first_name": "Sam", "last_name": "Limit", "email": email,
            "password": "Correct-horse-9", "password2": "Correct-horse-9"}


def test_the_sixth_sign_up_from_one_address_is_refused_and_creates_no_user(app, counters, signed_up_cleanup):
    from app import db

    environ = {"REMOTE_ADDR": "198.51.100.%d" % (20 + uuid.uuid4().int % 200)}
    tag = uuid.uuid4().hex[:8]
    emails = ["signup-limit-%s-%02d@example.com" % (tag, n) for n in range(40)]
    responses = []
    for email in emails:
        client = app.test_client()  # a fresh session each time
        responses.append(client.post("/account/register", data=_signup_form(email), environ_overrides=environ))
    codes = [r.status_code for r in responses]
    assert 429 not in codes[:5], codes
    assert set(codes[5:]) == {429}, codes
    refused = responses[5]
    assert "Try again in" in refused.get_data(as_text=True)
    _headers_say_what_is_left(refused)
    assert refused.headers["RateLimit-Remaining"] == "0" and int(refused.headers["Retry-After"]) > 0
    with app.app_context(), db.engine.connect() as connection:
        created = [r[0] for r in connection.execute(
            text("SELECT email FROM users WHERE email LIKE :p"), {"p": "signup-limit-%s-%%" % tag})]
    assert sorted(created) == sorted(emails[:5]), "a user was created after the refusal: %s" % created


def test_the_refusal_page_offers_a_signed_out_visitor_sign_in_not_the_dashboard(app, counters):
    environ = {"REMOTE_ADDR": "198.51.100.%d" % (20 + uuid.uuid4().int % 200)}
    tag = uuid.uuid4().hex[:8]
    for n in range(5):
        app.test_client().post("/account/register", data=_signup_form("signup-limit-%s-%d@example.com" % (tag, n)),
                               environ_overrides=environ)
    page = app.test_client().post("/account/register", data=_signup_form("signup-limit-%s-x@example.com" % tag),
                                  environ_overrides=environ)
    html = page.get_data(as_text=True)
    assert page.status_code == 429
    assert "Go to Dashboard" not in html
    assert "Sign in" in html and "/account/login" in html
    assert "Try again in about 1 hour" in html


def test_resending_the_confirmation_link_is_capped_per_address(app, counters, db_session, make_org, login_as):
    from app import db
    from app.models.user import User

    org = make_org("resend")
    user = User(email="resend-%s@example.test" % uuid.uuid4().hex[:8], organization_id=org.id, confirmed=False)
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    messages = []
    for _ in range(5):
        login_as(client, user)
        # The route's own per-minute limit is a different one; let its minute pass between tries.
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM rate_limit_counters WHERE key LIKE :p"),
                               {"p": "route:%user:" + str(user.id) + ":%"})
        response = client.post("/account/confirm-account", follow_redirects=True)
        messages.append(response.get_data(as_text=True))
    assert "several times this hour" not in "".join(messages[:3])
    assert "several times this hour" in messages[3] and "several times this hour" in messages[4]
