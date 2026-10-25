"""Every signed-in request is recorded with its outcome, and so are security events.

Two real organisations throughout: one organisation's entries are never read by
the other. Uses the shared fixtures in tests/conftest.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, *, admin=False, enterprise_role="solution_architect"):
    from app.models.user import Role, User

    user = User(
        email=f"activity-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Activity",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.password = "correct-horse-battery"
    role_name = "Administrator" if admin else "User"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _entries(org, table):
    from app.models.audit_log import AuditLog

    return (
        AuditLog.query.filter(AuditLog.org_predicate(org.id), AuditLog.table_name == table)
        .order_by(AuditLog.id)
        .all()
    )


def _set_switch(db_session, on):
    import json

    from app.services import activity_record

    db_session.execute(
        text(
            "INSERT INTO system_settings (key, value, updated_at) VALUES (:k, :v, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()"
        ),
        {"k": activity_record.SETTING_KEY, "v": json.dumps(on)},
    )
    activity_record.clear_setting_cache()


@pytest.fixture(autouse=True)
def _switch_back(db_session):
    from app.services import activity_record

    activity_record.clear_setting_cache()
    yield
    activity_record.clear_setting_cache()


def test_a_request_is_recorded_with_route_method_outcome_and_duration(db_session, make_org, client, login_as):
    org_a, org_b = make_org("act-a"), make_org("act-b")
    user_a = _user(db_session, org_a)
    _user(db_session, org_b)
    login_as(client, user_a)
    assert client.get("/dashboard/overview").status_code in (200, 302)

    rows = _entries(org_a, "page_use")
    assert rows, "no page-use entry written"
    detail = rows[-1].new_value
    assert rows[-1].user_id == user_a.id and rows[-1].organization_id == org_a.id
    assert detail["method"] == "GET" and detail["route"] and detail["outcome"] == "succeeded"
    assert isinstance(detail["duration_ms"], int)
    assert _entries(org_b, "page_use") == []


def test_a_refused_request_is_recorded_as_refused(db_session, make_org, client, login_as):
    org = make_org("act-refuse")
    user = _user(db_session, org)  # an ordinary user, not an administrator
    login_as(client, user)
    resp = client.get("/admin/audit-log")
    assert resp.status_code == 403

    page_use = _entries(org, "page_use")[-1].new_value
    assert page_use["outcome"] == "refused" and page_use["route"] == "admin.audit_log_viewer"
    events = [r.new_value["event"] for r in _entries(org, "security_event")]
    assert events == ["permission_refused"]


def test_an_export_is_recorded(db_session, make_org, client, login_as):
    org = make_org("act-export")
    admin = _user(db_session, org, admin=True)
    login_as(client, admin)
    assert client.get("/admin/audit-log?export=csv").status_code == 200

    events = [r.new_value for r in _entries(org, "security_event")]
    assert [e["event"] for e in events] == ["export"]
    assert events[0]["route"] == "admin.audit_log_viewer"


def test_a_failed_sign_in_is_recorded_against_the_account(db_session, make_org, client):
    org_a, org_b = make_org("act-fail-a"), make_org("act-fail-b")
    user = _user(db_session, org_a)
    _user(db_session, org_b)
    resp = client.post("/account/login", data={"email": user.email, "password": "wrong-password-here"})
    assert resp.status_code == 200

    from app.models.audit_log import AuditLog

    rows = AuditLog.query.filter(AuditLog.org_predicate(org_a.id), AuditLog.action == "login_failed").all()
    assert [r.user_id for r in rows] == [user.id]
    assert AuditLog.query.filter(AuditLog.org_predicate(org_b.id), AuditLog.action == "login_failed").count() == 0


def test_a_sign_in_is_recorded(db_session, make_org, client):
    org = make_org("act-in")
    user = _user(db_session, org)
    client.post("/account/login", data={"email": user.email, "password": "correct-horse-battery"})

    from app.models.audit_log import AuditLog

    assert AuditLog.query.filter(AuditLog.org_predicate(org.id), AuditLog.action == "login").count() == 1


def test_two_factor_and_recovery_events_are_recorded(app, db_session, make_org):
    from app.modules.account.mfa_flow import run_mfa_challenge
    from app.services import mfa_service

    org = make_org("act-mfa")
    user = _user(db_session, org, admin=True)
    secret = mfa_service.generate_secret()
    import pyotp

    codes = mfa_service.enroll(user, secret, pyotp.TOTP(secret).now())
    with app.test_request_context("/account/mfa-challenge", method="POST", data={"code": "000000"}):
        run_mfa_challenge(user, lambda u: "done")
    with app.test_request_context(
        "/account/mfa-challenge", method="POST", data={"code": codes[0], "use_recovery": "1"}
    ):
        assert run_mfa_challenge(user, lambda u: "done") == "done"

    events = [r.new_value["event"] for r in _entries(org, "security_event")]
    assert events == ["two_factor_failed", "recovery_code_used"]


def test_a_password_reset_is_recorded(db_session, make_org):
    from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken
    from app.modules.account.services.account_service import AccountService

    org = make_org("act-reset")
    user = _user(db_session, org)
    _, token = AccountToken.issue(user, PURPOSE_PASSWORD_RESET)
    ok, _ = AccountService.reset_password(token, "a-brand-new-password-1")
    assert ok
    assert [r.new_value["event"] for r in _entries(org, "security_event")] == ["password_reset"]


def test_another_organisations_entries_are_never_listed(db_session, make_org, client, login_as):
    org_a, org_b = make_org("act-iso-a"), make_org("act-iso-b")
    admin_a = _user(db_session, org_a, admin=True)
    admin_b = _user(db_session, org_b, admin=True)
    login_as(client, admin_a)
    client.get("/dashboard/overview")
    login_as(client, admin_b)
    client.get("/dashboard/overview")
    resp = client.get("/admin/audit-log?export=csv")
    body = resp.get_data(as_text=True)
    assert _entries(org_a, "page_use"), "organisation A's own page use was not recorded"
    assert not any(r.user_id == admin_a.id for r in _entries(org_b, "page_use"))
    assert org_a.name not in body and admin_a.email not in body
    assert "dashboard" in body and org_b.name in body  # organisation B sees its own entries only


def test_the_switch_turns_page_use_off_and_leaves_security_events_on(db_session, make_org, client, login_as):
    org = make_org("act-switch")
    user = _user(db_session, org)
    _set_switch(db_session, False)
    login_as(client, user)
    assert client.get("/admin/audit-log").status_code == 403

    assert _entries(org, "page_use") == []
    assert [r.new_value["event"] for r in _entries(org, "security_event")] == ["permission_refused"]

    _set_switch(db_session, True)
    login_as(client, user)
    client.get("/dashboard/overview")
    assert _entries(org, "page_use")


def test_entries_older_than_400_days_are_removed_and_the_chain_still_verifies(db_session, make_org):
    from app.models.audit_log import AuditLog
    from app.services import activity_record

    org = make_org("act-keep")
    user = _user(db_session, org)
    old = AuditLog.log(organization_id=org.id, user_id=user.id, action="request", table_name="page_use",
                       new_value={"route": "old", "method": "GET", "outcome": "succeeded"})
    db_session.execute(text(
        "UPDATE soc2_audit_log SET created_at = :t WHERE id = :i"), {"t": datetime.utcnow() - timedelta(days=401), "i": old.id})
    for n in range(2):
        AuditLog.log(organization_id=org.id, user_id=user.id, action="request", table_name="page_use",
                     new_value={"route": f"new{n}", "method": "GET", "outcome": "succeeded"})
    assert activity_record.purge_expired() >= 1
    routes = [r.new_value["route"] for r in _entries(org, "page_use")]
    assert routes == ["new0", "new1"]
    assert AuditLog.verify_chain(org.id)["status"] == "intact"
