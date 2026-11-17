"""Two-factor recovery codes: stored only as salted hashes, shown once when
made, and each one signs in exactly once."""

from __future__ import annotations

import uuid

import pyotp
import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")

_PASSWORD = "Str0ng!Passw0rd"


def _make_admin(db_session, org):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"recovery-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    return user


def _enrol(db_session, user):
    from app.services import mfa_service

    secret = pyotp.random_base32()
    codes = mfa_service.enroll(user, secret, pyotp.TOTP(secret).now())
    db_session.commit()
    return codes


def _start_sign_in(app, user):
    client = app.test_client()
    client.post(
        "/account/login",
        data={"email": user.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    return client


def _signed_in(client):
    return client.get("/dashboard/overview").status_code == 200


def test_recovery_codes_are_not_readable_from_the_database(app, db_session, make_org):
    admin = _make_admin(db_session, make_org("mfa-rc-store"))
    codes = _enrol(db_session, admin)
    assert len(codes) == 8

    raw = db_session.execute(
        db.text("SELECT mfa_recovery_codes FROM users WHERE id = :i"), {"i": admin.id}
    ).scalar()
    for code in codes:
        assert code not in raw
    import json

    assert len(json.loads(raw)) == 8


def test_a_recovery_code_signs_in_once_and_fails_the_second_time(app, db_session, make_org):
    admin = _make_admin(db_session, make_org("mfa-rc-once"))
    codes = _enrol(db_session, admin)

    first = _start_sign_in(app, admin)
    resp = first.post(
        "/account/mfa-challenge", data={"code": codes[0], "use_recovery": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert _signed_in(first)

    second = _start_sign_in(app, admin)
    second.post("/account/mfa-challenge", data={"code": codes[0], "use_recovery": "1"})
    assert not _signed_in(second)

    # A different, unused code still works.
    third = _start_sign_in(app, admin)
    third.post("/account/mfa-challenge", data={"code": codes[1], "use_recovery": "1"})
    assert _signed_in(third)


def test_a_wrong_recovery_code_does_not_sign_in(app, db_session, make_org):
    admin = _make_admin(db_session, make_org("mfa-rc-wrong"))
    _enrol(db_session, admin)

    client = _start_sign_in(app, admin)
    resp = client.post(
        "/account/mfa-challenge", data={"code": "ZZZZZZZZZZ", "use_recovery": "1"},
        follow_redirects=True,
    )
    assert b"not accepted" in resp.data
    assert not _signed_in(client)


def test_a_recovery_code_does_not_work_without_asking_for_recovery(app, db_session, make_org):
    admin = _make_admin(db_session, make_org("mfa-rc-plain"))
    codes = _enrol(db_session, admin)

    client = _start_sign_in(app, admin)
    client.post("/account/mfa-challenge", data={"code": codes[0]})
    assert not _signed_in(client)


def test_another_organisations_administrator_cannot_use_my_recovery_code(app, db_session, make_org):
    mine = _make_admin(db_session, make_org("mfa-rc-org-a"))
    theirs = _make_admin(db_session, make_org("mfa-rc-org-b"))
    my_codes = _enrol(db_session, mine)
    _enrol(db_session, theirs)

    client = _start_sign_in(app, theirs)
    client.post("/account/mfa-challenge", data={"code": my_codes[0], "use_recovery": "1"})
    assert not _signed_in(client)

    # And the code was not consumed from the owner's list by that attempt.
    owner = _start_sign_in(app, mine)
    owner.post("/account/mfa-challenge", data={"code": my_codes[0], "use_recovery": "1"})
    assert _signed_in(owner)
