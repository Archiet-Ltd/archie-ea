"""Administrators get a shorter idle window and an absolute session cap
(R1-B12 PR 2, TB-0144/PB-0100) on top of the platform-wide idle timeout
every user already has (app/_bootstrap/session_policy.py, finding F-07).

Uses the shared ``login_as`` fixture (tests/conftest.py) to reach an
authenticated session without driving the password/MFA-challenge form
flow -- these tests are about the session-policy hook, not the login flow
itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _backdate_session(db_session, sid, seconds):
    """Set a session row's created_at to ``seconds`` ago, computed by
    Postgres's own now() - interval so it matches what
    session_registry.age_seconds will measure regardless of the server's
    session timezone."""
    from app import db
    from app.models.user_session import UserSession

    db_session.execute(
        db.update(UserSession)
        .where(UserSession.sid == sid)
        .values(created_at=db.text(f"now() - interval '{seconds} seconds'"))
    )
    db_session.commit()


def _make_user(db_session, org, *, platform_admin=False, org_admin=False):
    from app.models import Role
    from app.models.user import User

    # is_org_admin is a derived property (app/models/user.py): it reflects
    # Permission.ADMINISTER via is_admin(), not a column that can be set
    # directly on a user with no role. Assign the real Administrator role
    # when org_admin is wanted, same as every other test in this session
    # that needs a real organisation administrator.
    role = None
    if org_admin:
        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            pytest.skip("no Administrator role seeded in this database")

    user = User(
        email=f"sess-admin-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id,
        confirmed=True,
        is_platform_admin=platform_admin,
        role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def test_a_platform_admin_is_idle_timed_out_sooner_than_an_ordinary_user(app, db_session, make_org, login_as):
    from app._bootstrap.session_policy import LAST_ACTIVITY_KEY
    from app.models.user_session import UserSession

    org = make_org("adminidle")
    admin = _make_user(db_session, org, platform_admin=True)
    db_session.commit()

    client = app.test_client()
    login_as(client, admin)
    with client.session_transaction() as sess:
        sid = sess["_sid"]

    admin_idle = app.config.get("SESSION_IDLE_TIMEOUT_ADMIN")
    if admin_idle is None:
        from app._bootstrap.session_policy import _idle_seconds_admin

        admin_idle = _idle_seconds_admin(app)
    platform_idle = app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
    assert admin_idle < platform_idle, (
        "the admin idle window must be strictly shorter than the platform default"
    )

    # Stale by more than the ADMIN window but still within the platform
    # default -- an ordinary user with this gap would stay logged in.
    stale = int((datetime.now(timezone.utc) - timedelta(seconds=admin_idle + 30)).timestamp())
    with client.session_transaction() as sess:
        sess[LAST_ACTIVITY_KEY] = stale

    resp = client.get("/dashboard/overview")
    assert resp.status_code in (302, 401)

    row = db_session.get(UserSession, sid)
    assert row is not None
    assert row.revoked_at is not None
    assert row.revoked_reason == "idle_timeout"


def test_an_ordinary_users_session_survives_the_admin_idle_window(app, db_session, make_org, login_as):
    """The admin-only idle window must not leak onto a non-admin session --
    an ordinary user stale by more than the admin window, but less than the
    platform default, stays logged in."""
    from app._bootstrap.session_policy import LAST_ACTIVITY_KEY, _idle_seconds_admin
    from app.models.user import User
    from app.models.user_session import UserSession

    org = make_org("ordinaryidle")
    user = User(
        email=f"sess-plain-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id, confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()

    admin_idle = _idle_seconds_admin(app)
    platform_idle = app.config["SESSION_IDLE_TIMEOUT_SECONDS"]
    if admin_idle + 30 >= platform_idle:
        pytest.skip("platform default idle window is not wider than the admin window in this config")

    client = app.test_client()
    login_as(client, user)
    with client.session_transaction() as sess:
        sid = sess["_sid"]

    stale = int((datetime.now(timezone.utc) - timedelta(seconds=admin_idle + 30)).timestamp())
    with client.session_transaction() as sess:
        sess[LAST_ACTIVITY_KEY] = stale

    client.get("/dashboard/overview")

    row = db_session.get(UserSession, sid)
    assert row is not None
    assert row.revoked_at is None, "a non-admin session must not be timed out by the admin idle window"


def test_an_org_admins_session_expires_at_the_absolute_cap_even_while_active(app, db_session, make_org, login_as):
    """An administrator's session is capped by age from creation
    (session_registry.age_seconds), not only by idleness -- unlike
    PERMANENT_SESSION_LIFETIME, this cannot be extended by continued
    activity refreshing the cookie."""
    from app._bootstrap.session_policy import LAST_ACTIVITY_KEY, _absolute_seconds_admin

    org = make_org("adminabsolute")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    client = app.test_client()
    login_as(client, admin)
    with client.session_transaction() as sess:
        sid = sess["_sid"]

    absolute = _absolute_seconds_admin(app)
    # Backdated with Postgres's own now() - interval, not a Python
    # datetime, so this stays correct on a server whose session timezone
    # is not UTC (this project's local dev Postgres is not) -- the same
    # reason app.services.session_registry.age_seconds computes age in SQL
    # rather than comparing the naive created_at column against Python's
    # own UTC clock.
    _backdate_session(db_session, sid, absolute + 60)

    # Mark the session as freshly active -- the absolute cap must still
    # apply even though the idle check alone would let this request through.
    with client.session_transaction() as sess:
        sess[LAST_ACTIVITY_KEY] = int(datetime.now(timezone.utc).timestamp())

    resp = client.get("/dashboard/overview")
    assert resp.status_code in (302, 401)

    from app.models.user_session import UserSession

    row = db_session.get(UserSession, sid)
    assert row.revoked_at is not None
    assert row.revoked_reason == "admin_absolute_timeout"


def test_an_ordinary_users_session_has_no_absolute_cap_from_this_feature(app, db_session, make_org, login_as):
    """The admin absolute cap must not apply to a non-admin session."""
    from app._bootstrap.session_policy import LAST_ACTIVITY_KEY, _absolute_seconds_admin
    from app.models.user import User
    from app.models.user_session import UserSession

    org = make_org("ordinaryabsolute")
    user = User(
        email=f"sess-plain2-{uuid.uuid4().hex[:10]}@example.com",
        organization_id=org.id, confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()

    client = app.test_client()
    login_as(client, user)
    with client.session_transaction() as sess:
        sid = sess["_sid"]

    absolute = _absolute_seconds_admin(app)
    _backdate_session(db_session, sid, absolute + 60)

    with client.session_transaction() as sess:
        sess[LAST_ACTIVITY_KEY] = int(datetime.now(timezone.utc).timestamp())

    client.get("/dashboard/overview")

    row = db_session.get(UserSession, sid)
    assert row.revoked_at is None
