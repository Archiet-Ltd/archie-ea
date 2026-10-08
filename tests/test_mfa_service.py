"""Multi-factor authentication for administrators (R1-B12 PR 2,
TB-0144/PB-0100).
"""

from __future__ import annotations

import uuid

import pyotp
import pytest

from app.services import mfa_service


class _FakeUser:
    def __init__(self, *, is_org_admin=False, is_platform_admin=False, email="a@example.com"):
        self.is_org_admin = is_org_admin
        self.is_platform_admin = is_platform_admin
        self.email = email
        self.mfa_secret = None
        self.mfa_enabled = False


def test_required_for_is_false_for_an_ordinary_user():
    assert mfa_service.required_for(_FakeUser()) is False


def test_required_for_is_true_for_an_org_admin():
    assert mfa_service.required_for(_FakeUser(is_org_admin=True)) is True


def test_required_for_is_true_for_a_platform_admin():
    assert mfa_service.required_for(_FakeUser(is_platform_admin=True)) is True


def test_required_for_is_false_for_none():
    assert mfa_service.required_for(None) is False


def test_generate_secret_returns_a_usable_base32_secret():
    secret = mfa_service.generate_secret()
    # A valid secret produces a 6-digit code without raising.
    code = pyotp.TOTP(secret).now()
    assert len(code) == 6
    assert code.isdigit()


def test_provisioning_uri_names_the_user_and_the_platform():
    user = _FakeUser(email="alice@acme.com")
    secret = mfa_service.generate_secret()

    uri = mfa_service.provisioning_uri(user, secret)

    assert uri.startswith("otpauth://totp/")
    assert "alice%40acme.com" in uri or "alice@acme.com" in uri


def test_verify_code_accepts_a_currently_valid_code():
    secret = mfa_service.generate_secret()
    code = pyotp.TOTP(secret).now()

    assert mfa_service.verify_code(secret, code) is True


def test_verify_code_refuses_a_wrong_code():
    secret = mfa_service.generate_secret()
    wrong = pyotp.TOTP(secret).now()
    # Ensure the "wrong" code really differs from the current one.
    wrong = "000000" if wrong != "000000" else "111111"

    assert mfa_service.verify_code(secret, wrong) is False


def test_verify_code_refuses_empty_inputs():
    secret = mfa_service.generate_secret()

    assert mfa_service.verify_code(secret, "") is False
    assert mfa_service.verify_code("", "123456") is False
    assert mfa_service.verify_code(None, None) is False


def test_enroll_persists_the_secret_and_enables_mfa(app, db_session, make_org):
    from app.models import Role
    from app.models.user import User

    org = make_org("mfa-enroll")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"mfa-{uuid.uuid4().hex[:6]}@example.test", first_name="Admin", last_name="Tester",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()

    secret = mfa_service.generate_secret()
    code = pyotp.TOTP(secret).now()

    mfa_service.enroll(user, secret, code)

    assert user.mfa_enabled is True
    assert user.mfa_secret == secret


def test_enroll_refuses_a_wrong_code_and_does_not_persist(app, db_session, make_org):
    from app.models import Role
    from app.models.user import User

    org = make_org("mfa-enroll-wrong")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"mfa-{uuid.uuid4().hex[:6]}@example.test", first_name="Admin", last_name="Tester",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()

    secret = mfa_service.generate_secret()

    with pytest.raises(mfa_service.MFAError):
        mfa_service.enroll(user, secret, "000000")

    assert user.mfa_enabled is False
    assert user.mfa_secret is None


def test_verify_login_code_is_false_when_mfa_is_not_enabled():
    user = _FakeUser()
    user.mfa_secret = mfa_service.generate_secret()
    user.mfa_enabled = False

    assert mfa_service.verify_login_code(user, "123456") is False


def test_verify_login_code_checks_the_enrolled_secret():
    user = _FakeUser()
    user.mfa_secret = mfa_service.generate_secret()
    user.mfa_enabled = True
    code = pyotp.TOTP(user.mfa_secret).now()

    assert mfa_service.verify_login_code(user, code) is True
    assert mfa_service.verify_login_code(user, "000000" if code != "000000" else "111111") is False
