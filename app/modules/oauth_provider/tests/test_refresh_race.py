"""Regression test: concurrent refresh-grant requests on the SAME refresh
token must not all succeed.

Before this fix, ``OAuthToken.revoke()`` was a plain, unconditional
set-and-commit, and ``_token_refresh`` read ``old_token.is_refresh_active``
(a property over the in-memory ``revoked_at``/``refresh_expires_at``
columns) and only *afterwards* called ``old_token.revoke()`` — a
check-then-act with no row lock or conditional UPDATE in between. Several
concurrent refresh-grant requests presenting the same refresh token could
all read ``is_refresh_active == True`` before any of them had committed a
revocation, so every one of them proceeded to mint its own fresh token pair.
Someone who steals a refresh token and races the legitimate client's own
rotation could keep their own separate, undetected token family — revoking
one pair would not revoke the others.

Why this test cannot use the shared ``db_session`` fixture
------------------------------------------------------------
``db_session`` (tests/conftest.py) substitutes a session class that pins
*every* session created anywhere in the process to ONE already-open
connection per engine for the duration of the test, specifically so the
test's writes roll back at teardown instead of being committed for real.
Concurrent worker threads sharing that single DBAPI connection would not be
exercising independent transactions at all — the very thing a check-then-act
race needs to actually race. ``tests/test_concurrent_application_create.py``
hit this exact trap first (see its own module docstring and its
``independent_application_race`` fixture) and the fix is the same here:
setup and teardown below use the real, un-patched scoped session (bound
directly to the NullPool-backed test engine — see ``TestingConfig`` in
config.py) and commit for real, cleaning up by hand in a ``finally`` block.

Why the race is forced rather than hoped for
---------------------------------------------
Plain unsynchronized threads racing a tiny in-process window is not a
reliable reproduction — it would pass most of the time even against the old,
buggy code, for the same reason two purely sequential calls would (the
exact failure this brief warns against). Instead this test uses a
SQLAlchemy ``after_cursor_execute`` hook on the shared engine (the same
technique ``tests/test_evidence_head_concurrency.py`` uses for a different
check-then-act race) to pause every worker thread immediately after the
SELECT that ``OAuthToken.find_by_refresh_token`` issues — the exact
statement backing ``_token_refresh``'s ``is_refresh_active`` check — and
release all of them at once, only once every worker has reached it. That
deterministically reproduces the worst case described in the fix-round
brief: all six requests have genuinely read the row as still active before
any of them attempts to revoke/rotate it.

This was confirmed to fail against the pre-fix code: temporarily reverting
``OAuthToken.revoke()`` and the ``_token_refresh`` check (``git stash`` back
to the commit before this round) and re-running
``test_concurrent_refresh_requests_mint_only_one_new_token_pair`` produces
more than one 200 response and more than one new ``oauth_tokens`` row for
the same rotation — see the build report for the exact run. Restoring the
fix makes it pass again.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import traceback
import urllib.parse
import uuid

import pytest
from sqlalchemy import event


def _pkce_pair() -> tuple[str, str]:
    """Generate a code_verifier and its S256 code_challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    assert base, "PUBLIC_BASE_URL must be set to run this module — see test_oauth_flow.py's module docstring"
    return f"{base}/mcp"


def _register_client(redirect_uris="http://localhost/callback"):
    from app.modules.oauth_provider.models import OAuthClient

    return OAuthClient.register(client_name="Refresh Race Test Client", redirect_uris=redirect_uris)


def _make_org_committed(label: str):
    """Like tests/conftest.py's ``make_org``, but a real, standalone commit —
    see the module docstring for why this test cannot depend on db_session."""
    from app import db
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Refresh Race {label} {suffix}", slug=f"refresh-race-{label}-{suffix}")
    db.session.add(org)
    db.session.commit()
    return org


def _make_user_committed(org_id: int, email: str):
    """Like test_oauth_flow.py's ``_make_user``, but a real, standalone commit."""
    from app import db
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email,
        first_name="Refresh",
        last_name="Race",
        organization_id=org_id,
        role=role,
        is_org_admin=True,
        confirmed=True,
    )
    user.password = "test"
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def issued_refresh_token(app, _schema, login_as):
    """A real, committed access+refresh token pair, independent of the
    db_session fixture. Cleans up every row it creates, the same way
    tests/test_concurrent_application_create.py's
    ``independent_application_race`` fixture does.
    """
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.oauth_provider.models import OAuthClient, OAuthToken

    with app.app_context():
        org = _make_org_committed("refresh")
        user = _make_user_committed(org.id, f"refresh-race-{uuid.uuid4().hex[:8]}@example.com")
        oauth_client = _register_client()
        org_id, user_id, oauth_client_pk, client_id = org.id, user.id, oauth_client.id, oauth_client.client_id

    setup_client = app.test_client()
    login_as(setup_client, user)

    verifier, challenge = _pkce_pair()
    resource = _resource(app)
    resp = setup_client.post(
        "/oauth/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
            "decision": "allow",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)
    location = resp.headers["Location"]
    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

    resp = setup_client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://localhost/callback",
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    tokens = resp.get_json()

    state = {
        "org_id": org_id,
        "user_id": user_id,
        "oauth_client_pk": oauth_client_pk,
        "client_id": client_id,
        "resource": resource,
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens["access_token"],
    }
    try:
        yield state
    finally:
        with app.app_context():
            OAuthToken.query.filter_by(client_id=state["client_id"]).delete(synchronize_session=False)
            OAuthClient.query.filter_by(id=state["oauth_client_pk"]).delete(synchronize_session=False)
            # UserSession rows cascade on DELETE (ondelete="CASCADE" on
            # user_sessions.user_id — see app/models/user_session.py), so the
            # login-registry row login_as/mint_test_sid wrote is removed by
            # the database itself when the user row goes.
            User.query.filter_by(id=state["user_id"]).delete(synchronize_session=False)
            Organization.query.filter_by(id=state["org_id"]).delete(synchronize_session=False)
            db.session.commit()


def _looks_like_refresh_token_lookup(statement: str) -> bool:
    """True only for the SELECT ``OAuthToken.find_by_refresh_token`` issues
    (i.e. one that filters *by* ``refresh_token``), not for every SELECT that
    happens to mention the column.

    ``OAuthToken.query`` always selects every mapped column, so a plain
    SELECT-by-primary-key (e.g. the expired-attribute reload SQLAlchemy
    issues when a later flush in the same request needs the pre-commit value
    of an attribute on an already-expired instance — harmless and expected
    once ``revoke()``'s own commit expires the session's objects) also
    contains the substring ``oauth_tokens.refresh_token`` in its column list.
    Requiring the match to fall in the WHERE clause specifically — the
    column SQLAlchemy actually filters by — is what tells the two apart. The
    revoke UPDATE this test is racing against never references the
    ``refresh_token`` column at all (it filters on ``id`` and
    ``revoked_at``), so there is no ambiguity between the three statements.
    """
    lowered = statement.lower()
    if not lowered.lstrip().startswith("select") or "for update" in lowered:
        return False
    where_index = lowered.find(" where ")
    if where_index == -1:
        return False
    return "oauth_tokens.refresh_token" in lowered[where_index:]


def test_concurrent_refresh_requests_mint_only_one_new_token_pair(app, issued_refresh_token):
    """Six simultaneous refresh-grant requests on one refresh token: exactly
    one must win with a fresh token pair, every other one must be refused,
    and the database must hold exactly one new token row for the rotation —
    not six."""
    from app import db
    from app.modules.oauth_provider.models import OAuthToken

    fixture = issued_refresh_token
    workers = 6
    barrier = threading.Barrier(workers)
    results: dict[int, tuple[int, dict]] = {}
    errors: dict[int, str] = {}

    def paused_lookup(_connection, _cursor, statement, _parameters, _context, _executemany):
        if threading.current_thread().name.startswith("refresh-race-worker-") and _looks_like_refresh_token_lookup(statement):
            barrier.wait(timeout=20)

    def refresh(index):
        try:
            worker_client = app.test_client()
            resp = worker_client.post(
                "/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": fixture["refresh_token"],
                    "client_id": fixture["client_id"],
                    "resource": fixture["resource"],
                },
            )
            results[index] = (resp.status_code, resp.get_json())
        except BaseException:
            errors[index] = traceback.format_exc()
            try:
                barrier.abort()
            except threading.BrokenBarrierError:
                pass

    with app.app_context():
        engine = db.engine
    event.listen(engine, "after_cursor_execute", paused_lookup)
    threads = [
        threading.Thread(target=refresh, args=(index,), name=f"refresh-race-worker-{index}", daemon=True)
        for index in range(workers)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        event.remove(engine, "after_cursor_execute", paused_lookup)

    assert all(not thread.is_alive() for thread in threads), "a refresh-race worker did not finish"
    assert not errors, "\n".join(errors.values())
    assert len(results) == workers

    successes = [(status, body) for status, body in results.values() if status == 200]
    failures = [(status, body) for status, body in results.values() if status != 200]

    assert len(successes) == 1, (
        f"expected exactly one winning refresh grant out of {workers} concurrent requests "
        f"on the same refresh token, got {len(successes)}: {results}"
    )
    assert len(failures) == workers - 1
    for status, body in failures:
        assert status == 400, (status, body)
        assert body.get("error") == "invalid_grant", body

    winning_body = successes[0][1]
    assert "access_token" in winning_body
    assert winning_body["refresh_token"] != fixture["refresh_token"]

    with app.app_context():
        rotated_rows = OAuthToken.query.filter_by(
            client_id=fixture["client_id"], grant_type="refresh_token"
        ).count()
        assert rotated_rows == 1, (
            f"{workers} concurrent identical refresh grants produced {rotated_rows} new token "
            "rows for this rotation — the old refresh token must only ever be redeemable once"
        )


def test_single_refresh_after_the_race_still_works_normally(app, issued_refresh_token):
    """The ordinary, non-concurrent path is unchanged by the atomic revoke:
    one refresh grant succeeds and mints a new pair, and the spent refresh
    token is rejected on reuse."""
    fixture = issued_refresh_token
    client = app.test_client()

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": fixture["refresh_token"],
            "client_id": fixture["client_id"],
            "resource": fixture["resource"],
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    new_tokens = resp.get_json()
    assert new_tokens["access_token"] != fixture["access_token"]
    assert new_tokens["refresh_token"] != fixture["refresh_token"]

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": fixture["refresh_token"],
            "client_id": fixture["client_id"],
            "resource": fixture["resource"],
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_grant"
