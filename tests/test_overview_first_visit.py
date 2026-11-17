"""``_is_first_visit`` decides a signed-in user's first visit from their own
``user_sessions`` rows only (round welcome01) -- no new column, no new migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest


def _user(db_session, org, first_name="Sam"):
    from app.models.user import Role, User

    Role.insert_roles()
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email="%s.%s@example.com" % (first_name.lower(), suffix),
        first_name=first_name,
        last_name="Starter",
        organization_id=org.id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _session_row(db_session, user_id, created_at):
    from app.models.user_session import UserSession

    row = UserSession(sid=uuid.uuid4().hex, user_id=user_id, created_at=created_at)
    db_session.add(row)
    db_session.flush()
    return row


def test_a_users_only_session_is_a_first_visit(app, db_session, make_org):
    from app.modules.dashboard.services.overview_figures import _is_first_visit

    user = _user(db_session, make_org("welcome01"))
    current = _session_row(db_session, user.id, datetime.utcnow())
    with app.test_request_context("/"):
        from flask import session

        session["_sid"] = current.sid
        assert _is_first_visit(user) is True


def test_an_earlier_session_for_the_same_user_makes_it_a_return_visit(app, db_session, make_org):
    from app.modules.dashboard.services.overview_figures import _is_first_visit

    user = _user(db_session, make_org("welcome01"))
    now = datetime.utcnow()
    _session_row(db_session, user.id, now - timedelta(days=1))
    current = _session_row(db_session, user.id, now)
    with app.test_request_context("/"):
        from flask import session

        session["_sid"] = current.sid
        assert _is_first_visit(user) is False


def test_another_users_earlier_session_is_not_counted(app, db_session, make_org):
    from app.modules.dashboard.services.overview_figures import _is_first_visit

    org = make_org("welcome01")
    sam = _user(db_session, org, "Sam")
    other = _user(db_session, org, "Other")
    now = datetime.utcnow()
    _session_row(db_session, other.id, now - timedelta(days=1))  # another user's earlier session
    current = _session_row(db_session, sam.id, now)
    with app.test_request_context("/"):
        from flask import session

        session["_sid"] = current.sid
        assert _is_first_visit(sam) is True


def test_no_session_id_is_not_a_first_visit(app, db_session, make_org):
    from app.modules.dashboard.services.overview_figures import _is_first_visit

    user = _user(db_session, make_org("welcome01"))
    with app.test_request_context("/"):
        assert _is_first_visit(user) is False
